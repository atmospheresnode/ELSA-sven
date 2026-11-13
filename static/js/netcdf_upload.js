/* Resumable NetCDF uploads for the bundle page, using the tus protocol (https://tus.io).
 *
 * Each file goes up in pieces. A dropped connection, a sleeping laptop or a closed tab costs at
 * most one piece: the server says how far it got and we carry on from there. Pieces are gzipped on
 * the way when that makes them smaller, which on model output saves 10 to 40% of the bytes; the
 * server unpacks them and stores the original file. Whether a file compresses is measured, not
 * guessed from its format: plenty of NetCDF-4 files are stored without compression.
 *
 * The page calls ElsaNetcdfUpload.start() with the files and a few callbacks, and
 * ElsaNetcdfUpload.showResumable() to offer interrupted uploads back after a reload.
 * Everything the server says is shown with textContent, never as markup.
 */
(function () {
   'use strict';

   var TUS_VERSION = '1.0.0';
   var STORE_PREFIX = 'elsaTus:';
   // No progress for this long means the connection is dead, even if the browser hasn't noticed.
   var STALL_MS = 45000;
   var RETRY_DELAYS = [1, 2, 4, 8, 15, 30];
   // Server errors we retry before giving up. Network drops are retried for as long as it takes.
   var MAX_SERVER_RETRIES = 8;
   // A piece that stalls or runs out of time is sent again at half the size, down to this.
   var MIN_CHUNK = 5 * 1024 * 1024;

   var NOT_NETCDF = ' is not a valid NetCDF file. Only NetCDF classic, 64-bit, or HDF5-based files are accepted.';

   function supported() {
      return typeof window.XMLHttpRequest === 'function' && typeof window.Promise === 'function' &&
         typeof Blob !== 'undefined' && typeof Blob.prototype.slice === 'function' &&
         typeof Blob.prototype.arrayBuffer === 'function';
   }

   function canCompress() {
      return typeof window.CompressionStream === 'function' && typeof window.Response === 'function';
   }

   function canHash() {
      return !!(window.crypto && window.crypto.subtle && window.isSecureContext);
   }

   // ---- small helpers ----

   function sleep(ms) {
      return new Promise(function (resolve) { setTimeout(resolve, ms); });
   }

   function base64Text(text) {
      var bytes = new TextEncoder().encode(text);
      var binary = '';
      for (var i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
      return btoa(binary);
   }

   function base64Bytes(buffer) {
      var bytes = new Uint8Array(buffer);
      var binary = '';
      for (var i = 0; i < bytes.length; i += 0x8000) {
         binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
      }
      return btoa(binary);
   }

   function humanSize(bytes) {
      var units = ['bytes', 'KB', 'MB', 'GB', 'TB'];
      var size = bytes, unit = 0;
      while (size >= 1000 && unit < units.length - 1) { size /= 1000; unit++; }
      return unit === 0 ? size + ' bytes' : size.toFixed(1) + ' ' + units[unit];
   }

   function humanDuration(seconds) {
      if (!isFinite(seconds) || seconds < 0) return '';
      if (seconds < 60) return 'under a minute';
      var minutes = Math.round(seconds / 60);
      if (minutes < 60) return 'about ' + minutes + ' min';
      var hours = Math.floor(minutes / 60);
      var rest = minutes % 60;
      return 'about ' + hours + ' h' + (rest ? ' ' + rest + ' min' : '');
   }

   function store() {
      try { return window.localStorage; } catch (e) { return null; }
   }

   function fingerprint(opts, file) {
      return STORE_PREFIX + opts.bundleId + ':' + opts.collectionId + ':' +
         [file.name, file.size, file.lastModified].join(':');
   }

   function remember(key, url) {
      var s = store();
      if (!s) return;
      try { s.setItem(key, JSON.stringify({ url: url, at: Date.now() })); } catch (e) {}
   }

   function recall(key) {
      var s = store();
      if (!s) return null;
      try {
         var entry = JSON.parse(s.getItem(key) || 'null');
         // Only ever a path on this site.
         if (entry && typeof entry.url === 'string' && entry.url.charAt(0) === '/' && entry.url.charAt(1) !== '/') {
            return entry.url;
         }
      } catch (e) {}
      return null;
   }

   function forget(key) {
      var s = store();
      if (s) { try { s.removeItem(key); } catch (e) {} }
   }

   // A request with the headers every call needs. Resolves with the xhr whatever the status.
   function request(method, url, opts, extra) {
      extra = extra || {};
      return new Promise(function (resolve) {
         var xhr = new XMLHttpRequest();
         xhr.open(method, url);
         xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
         if (method !== 'GET' && method !== 'HEAD') xhr.setRequestHeader('X-CSRFToken', opts.csrfToken);
         if (extra.tus !== false) xhr.setRequestHeader('Tus-Resumable', TUS_VERSION);
         var headers = extra.headers || {};
         Object.keys(headers).forEach(function (name) { xhr.setRequestHeader(name, headers[name]); });
         var lastProgress = Date.now();
         var stallTimer = null;
         if (extra.onProgress) {
            xhr.upload.addEventListener('progress', function (ev) {
               lastProgress = Date.now();
               extra.onProgress(ev.loaded, ev.total);
            });
            stallTimer = setInterval(function () {
               if (Date.now() - lastProgress > STALL_MS) { xhr.stalled = true; xhr.abort(); }
            }, 5000);
         }
         function done() {
            if (stallTimer) clearInterval(stallTimer);
            resolve(xhr);
         }
         xhr.addEventListener('load', done);
         xhr.addEventListener('error', done);
         xhr.addEventListener('abort', done);
         xhr.addEventListener('timeout', done);
         if (extra.register) extra.register(xhr);
         xhr.send(extra.body === undefined ? null : extra.body);
      });
   }

   function replyJson(xhr) {
      try { return JSON.parse(xhr.responseText); } catch (e) { return null; }
   }

   function serverMessage(xhr, fallback) {
      var reply = replyJson(xhr);
      if (reply && typeof reply.error === 'string') return reply.error;
      if (xhr.status === 403) return 'The server refused this upload. Please reload the page and pick the same files to carry on.';
      return fallback;
   }

   // Statuses worth another go: the network, a busy upload, a damaged piece, a server hiccup.
   function retriable(xhr) {
      return xhr.status === 0 || xhr.status === 408 || xhr.status === 423 || xhr.status === 429 || xhr.status === 460 ||
         xhr.status === 502 || xhr.status === 503 || xhr.status === 504 || xhr.status === 500;
   }

   // fatal: the whole batch has to stop (signed out, server full). Otherwise only this file failed.
   function UploadFailed(message, reply, fatal) {
      this.message = message;
      this.reply = reply || null;
      this.fatal = !!fatal;
   }

   // Statuses that say nothing in this batch can go up either.
   function stopsEverything(status) {
      return status === 401 || status === 403 || status === 429 || status === 507;
   }

   // First 8 bytes say what kind of file it is. null means not NetCDF at all.
   function sniffFile(file) {
      return file.slice(0, 8).arrayBuffer().then(function (buffer) {
         var b = new Uint8Array(buffer);
         if (b.length >= 4 && b[0] === 0x43 && b[1] === 0x44 && b[2] === 0x46 && (b[3] === 1 || b[3] === 2)) {
            return 'classic';
         }
         var hdf5 = [0x89, 0x48, 0x44, 0x46, 0x0d, 0x0a, 0x1a, 0x0a];
         if (b.length === 8 && hdf5.every(function (v, i) { return b[i] === v; })) return 'hdf5';
         return null;
      }).catch(function () { return null; });
   }

   // ---- one batch of files ----

   function Batch(files, opts) {
      this.files = files;
      this.opts = opts;
      // index -> why it can't go up, found when the files were picked
      this.skip = opts.skip || {};
      this.failures = [];
      this.cancelled = false;
      this.current = null;       // the xhr in flight
      this.currentUrl = null;    // the upload being sent
      this.finishing = false;
      var skip = this.skip;
      this.total = files.reduce(function (sum, f, i) { return skip[i] ? sum : sum + f.size; }, 0);
      this.doneBytes = 0;        // whole files already up
      this.samples = [];         // [time, bytes the server confirmed] for the speed reading
      this.rate = 0;
      this.replies = [];
      this.wakeLock = null;
   }

   Batch.prototype.status = function (text) {
      if (this.opts.onStatus) this.opts.onStatus(text);
   };

   // waiting, uploading, processing, added or failed, for that file's row in the list.
   Batch.prototype.fileState = function (index, state, text) {
      if (this.opts.onFileState) this.opts.onFileState(index, state, text);
   };

   // Position among the files actually being sent, for "file 2 of 4".
   Batch.prototype.position = function (index) {
      var skip = this.skip, n = 0, count = 0;
      for (var i = 0; i < this.files.length; i++) {
         if (skip[i]) continue;
         count++;
         if (i <= index) n++;
      }
      return { n: n, count: count };
   };

   // fromServer: the server has these bytes. Otherwise it's mid-piece, which the browser counts
   // as sent once they're in its own buffers, so it only moves the bar, never the speed.
   // fileDone: the file at index is finished with (fileBytes is then 0, it's in doneBytes).
   Batch.prototype.progress = function (index, fileBytes, fromServer, fileDone) {
      var now = Date.now();
      var confirmed = this.doneBytes + fileBytes;
      if (fromServer) {
         this.samples.push([now, confirmed]);
         while (this.samples.length > 2 && now - this.samples[0][0] > 90000) this.samples.shift();
         var first = this.samples[0];
         if (this.samples.length >= 3 && now - first[0] > 10000) {
            this.rate = (confirmed - first[1]) / ((now - first[0]) / 1000);
         }
      }
      var rate = this.rate;
      var where = this.position(index);
      if (this.opts.onProgress) {
         this.opts.onProgress({
            index: index,
            n: where.n,
            count: where.count,
            name: this.files[index].name,
            fileSent: fileDone ? this.files[index].size : fileBytes,
            fileSize: this.files[index].size,
            sent: confirmed,
            total: this.total,
            rate: rate,
            eta: rate > 0 ? (this.total - confirmed) / rate : Infinity
         });
      }
   };

   Batch.prototype.keepAwake = function (on) {
      var self = this;
      if (!('wakeLock' in navigator)) return;
      if (on && !self.wakeLock) {
         navigator.wakeLock.request('screen').then(function (lock) { self.wakeLock = lock; }).catch(function () {});
      } else if (!on && self.wakeLock) {
         self.wakeLock.release().catch(function () {});
         self.wakeLock = null;
      }
   };

   Batch.prototype.cancel = function () {
      this.cancelled = true;
      if (this.current) this.current.abort();
   };

   Batch.prototype.waitBeforeRetry = function (attempt, why) {
      var self = this;
      var wait = RETRY_DELAYS[Math.min(attempt, RETRY_DELAYS.length - 1)];
      if (!navigator.onLine) {
         self.status('Waiting for the network to come back...');
         return new Promise(function (resolve) {
            function back() { window.removeEventListener('online', back); clearInterval(poll); resolve(); }
            var poll = setInterval(function () { if (self.cancelled || navigator.onLine) back(); }, 1000);
            window.addEventListener('online', back);
         });
      }
      var left = wait;
      self.status(why + ' Retrying in ' + left + 's...');
      return new Promise(function (resolve) {
         var tick = setInterval(function () {
            left -= 1;
            if (self.cancelled || left <= 0) { clearInterval(tick); resolve(); return; }
            self.status(why + ' Retrying in ' + left + 's...');
         }, 1000);
      });
   };

   Batch.prototype.sniff = function (file) {
      return sniffFile(file);
   };

   Batch.prototype.create = function (file) {
      var opts = this.opts;
      var self = this;
      var metadata = 'filename ' + base64Text(file.name) + ',collection ' + base64Text(String(opts.collectionId));
      return request('POST', opts.createUrl, opts, {
         headers: { 'Upload-Length': String(file.size), 'Upload-Metadata': metadata },
         register: function (xhr) { self.current = xhr; }
      }).then(function (xhr) {
         if (xhr.status === 201) {
            var location = xhr.getResponseHeader('Location');
            // Resolved against this page, and it has to stay on this site.
            var resolved = new URL(location, window.location.href);
            if (resolved.origin !== window.location.origin) throw new UploadFailed('The server answered with an unexpected address.');
            return resolved.pathname;
         }
         if (self.cancelled) throw new UploadFailed('cancelled');
         if (stopsEverything(xhr.status)) {
            throw new UploadFailed(serverMessage(xhr, 'The upload could not be started.'), replyJson(xhr), true);
         }
         if (retriable(xhr)) return null;
         throw new UploadFailed(serverMessage(xhr, 'The upload could not be started.'), replyJson(xhr));
      });
   };

   // Where the server is with this upload, or null when it no longer has it.
   Batch.prototype.offsetOf = function (url) {
      var self = this;
      return request('HEAD', url, this.opts, { register: function (xhr) { self.current = xhr; } }).then(function (xhr) {
         if (xhr.status === 200) {
            return { offset: parseInt(xhr.getResponseHeader('Upload-Offset'), 10),
                     length: parseInt(xhr.getResponseHeader('Upload-Length'), 10) };
         }
         if (xhr.status === 404 || xhr.status === 410) return null;
         if (xhr.status === 401) throw new UploadFailed(serverMessage(xhr, 'Please sign in again.'));
         return undefined;  // could not tell, try again
      });
   };

   Batch.prototype.sendFile = async function (index) {
      var file = this.files[index];
      var opts = this.opts;
      var key = fingerprint(opts, file);
      var kind = await this.sniff(file);
      if (!kind) throw new UploadFailed('"' + file.name + '"' + NOT_NETCDF);

      var url = null, offset = 0, attempt = 0, failures = 0;
      var saved = recall(key);
      if (saved) {
         this.status('Checking for an earlier upload of ' + file.name + '...');
         var state = await this.offsetOf(saved);
         if (state && state.length === file.size) { url = saved; offset = state.offset; }
         else if (state === null) forget(key);
      }

      while (!url) {
         if (this.cancelled) throw new UploadFailed('cancelled');
         url = await this.create(file);
         if (!url) await this.waitBeforeRetry(attempt++, 'Could not reach the server.');
      }
      remember(key, url);
      this.currentUrl = url;
      // A fresh start for the speed reading: a resumed upload jumps straight to its offset.
      this.samples = [];
      this.progress(index, offset, true);

      var compress = canCompress() && opts.compress !== false;
      // Pieces that didn't shrink in a row, and how many to send plain before trying again.
      var poorRatios = 0;
      var skipping = 0;
      var chunk = opts.chunkSize;
      var self = this;
      var pending = null;

      async function prepare(at, size) {
         var stop = Math.min(at + size, file.size);
         var raw = await file.slice(at, stop).arrayBuffer();
         var headers = { 'Content-Type': 'application/offset+octet-stream' };
         if (canHash()) {
            headers['Upload-Checksum'] = 'sha256 ' + base64Bytes(await crypto.subtle.digest('SHA-256', raw));
         }
         var body = raw;
         if (compress && skipping > 0) {
            skipping--;
         } else if (compress) {
            var packed = await new Response(new Blob([raw]).stream().pipeThrough(new CompressionStream('gzip'))).arrayBuffer();
            if (packed.byteLength < raw.byteLength * 0.97) {
               body = packed;
               headers['Content-Encoding'] = 'gzip';
               poorRatios = 0;
            } else if (++poorRatios >= 3) {
               // Already compressed inside. Stop spending time on it, but look again later on.
               poorRatios = 0;
               skipping = 20;
            }
         }
         return { start: at, end: stop, headers: headers, body: body };
      }

      while (offset < file.size) {
         if (this.cancelled) throw new UploadFailed('cancelled');
         // The next piece is read, checksummed and gzipped while this one is on the wire,
         // so compressing costs no time of its own.
         var ready = pending && pending.start === offset && pending.chunk === chunk
            ? await pending.promise : await prepare(offset, chunk);
         pending = null;
         if (ready.end < file.size) {
            pending = { start: ready.end, chunk: chunk, promise: prepare(ready.end, chunk) };
            pending.promise.catch(function () {});
         }
         var headers = ready.headers;
         headers['Upload-Offset'] = String(offset);
         var body = ready.body;
         var end = ready.end;
         var rawLength = end - offset;
         var startOffset = offset;
         this.status(null);
         var xhr = await request('PATCH', url, opts, {
            headers: headers,
            body: body,
            register: function (x) { self.current = x; },
            onProgress: function (loaded, total) {
               // Count it in file bytes, so a gzipped piece fills the bar at the right speed.
               if (total) self.progress(index, startOffset + Math.min(rawLength, rawLength * loaded / total));
            }
         });
         this.current = null;
         if (this.cancelled) throw new UploadFailed('cancelled');

         if (xhr.status === 204) {
            offset = parseInt(xhr.getResponseHeader('Upload-Offset'), 10);
            if (!(offset >= 0)) {
               // No offset in the answer: ask the server rather than guess.
               var known = await this.offsetOf(url);
               if (!known) throw new UploadFailed('The server stopped answering properly. Please try again.');
               offset = known.offset;
            }
            attempt = 0;
            failures = 0;
            this.progress(index, offset, true);
            continue;
         }
         if (xhr.status === 409) {
            // Out of step, e.g. a piece that landed but whose answer was lost. Ask and carry on.
            var where = await this.offsetOf(url);
            if (where && where.length === file.size) { offset = where.offset; continue; }
         }
         if (xhr.status === 404 || xhr.status === 410) {
            forget(key);
            throw new UploadFailed(serverMessage(xhr, 'This upload is no longer on the server. Please start it again.'));
         }
         if (retriable(xhr) || xhr.stalled) {
            if (xhr.status !== 0 && !xhr.stalled && ++failures > MAX_SERVER_RETRIES) {
               throw new UploadFailed(serverMessage(xhr, 'The server kept failing. Please try again later.'));
            }
            if ((xhr.stalled || xhr.status === 408) && chunk > MIN_CHUNK) chunk = Math.max(MIN_CHUNK, Math.floor(chunk / 2));
            var why = xhr.status === 0 || xhr.stalled ? 'Connection lost.' :
               xhr.status === 408 ? 'The connection is slow.' :
               xhr.status === 460 ? 'A piece arrived damaged.' : 'The server is busy.';
            await this.waitBeforeRetry(attempt++, why);
            if (this.cancelled) throw new UploadFailed('cancelled');
            // The piece may have landed before the connection died.
            var now = await this.offsetOf(url);
            if (now === null) { forget(key); throw new UploadFailed('This upload is no longer on the server. Please start it again.'); }
            if (now) offset = now.offset;
            continue;
         }
         if (xhr.status === 415) forget(key);  // not NetCDF: the server threw it away
         throw new UploadFailed(serverMessage(xhr, 'The upload failed.'), replyJson(xhr),
                                xhr.status === 401 || xhr.status === 403);
      }
      return { url: url, key: key };
   };

   Batch.prototype.finish = async function (index, sent) {
      var self = this;
      var file = this.files[index];
      this.finishing = true;
      this.fileState(index, 'processing');
      if (this.opts.onFinishing) this.opts.onFinishing(index, file.name);
      for (var attempt = 0; ; attempt++) {
         var xhr = await request('POST', sent.url + 'finish/', this.opts, {
            tus: false, register: function (x) { self.current = x; }
         });
         this.current = null;
         if (xhr.status === 200) {
            forget(sent.key);
            this.finishing = false;
            return replyJson(xhr);
         }
         if (xhr.status === 400) {
            // Saved, but its metadata could not be read. Same as a form upload.
            forget(sent.key);
            this.finishing = false;
            throw new UploadFailed(serverMessage(xhr, 'The file was uploaded but could not be processed.'), replyJson(xhr));
         }
         if (stopsEverything(xhr.status)) {
            this.finishing = false;
            throw new UploadFailed(serverMessage(xhr, 'The file could not be added.'), replyJson(xhr), true);
         }
         if ((retriable(xhr) || xhr.status === 0) && attempt < MAX_SERVER_RETRIES) {
            await this.waitBeforeRetry(attempt, 'Could not finish ' + file.name + '.');
            continue;
         }
         throw new UploadFailed(serverMessage(xhr, 'The file was uploaded but could not be added to the bundle.'), replyJson(xhr));
      }
   };

   // Cancel the upload that was in flight, so its bytes don't sit on the server for a week.
   Batch.prototype.discardCurrent = async function () {
      var url = this.currentUrl;
      if (!url) return;
      for (var attempt = 0; attempt < 5; attempt++) {
         var xhr = await request('DELETE', url, this.opts, {});
         if (xhr.status === 204 || xhr.status === 404 || xhr.status === 410 || xhr.status === 409) return;
         await sleep(1000 * (attempt + 1));  // 423 while the server lets go of the aborted piece
      }
   };

   Batch.prototype.run = async function () {
      var opts = this.opts;
      var self = this;
      function guard(e) { e.preventDefault(); e.returnValue = ''; }
      window.addEventListener('beforeunload', guard);
      function wake() { if (document.visibilityState === 'visible') self.keepAwake(true); }
      document.addEventListener('visibilitychange', wake);
      this.keepAwake(true);
      var failures = this.failures;
      try {
         for (var i = 0; i < this.files.length; i++) {
            var file = this.files[i];
            if (this.skip[i]) {
               failures.push({ name: file.name, reason: this.skip[i] });
               this.fileState(i, 'failed', this.skip[i]);
               continue;
            }
            this.fileState(i, 'uploading', 0);
            try {
               var sent = await this.sendFile(i);
               this.currentUrl = null;
               this.replies.push(await this.finish(i, sent));
               this.fileState(i, 'added');
            } catch (err) {
               this.currentUrl = this.cancelled ? this.currentUrl : null;
               if (!(err instanceof UploadFailed)) err = new UploadFailed('Upload failed: ' + (err && err.message ? err.message : err));
               if (this.cancelled || err.message === 'cancelled' || err.fatal) throw err;
               // Only this file. Note why and carry on with the rest.
               failures.push({ name: file.name, reason: err.message });
               this.fileState(i, 'failed', err.message);
            }
            this.finishing = false;
            this.doneBytes += file.size;
            this.progress(i, 0, true, true);
         }
         if (this.replies.length) {
            var last = this.replies[this.replies.length - 1] || {};
            var merged = {
               collection_id: last.collection_id,
               collection_name: last.collection_name,
               tab_target: last.tab_target,
               files: [].concat.apply([], this.replies.map(function (r) { return (r && r.files) || []; })),
               not_added: failures
            };
            if (opts.onDone) opts.onDone(merged);
         } else if (opts.onError) {
            opts.onError(failures.length === 1 ? failures[0].reason
                         : 'None of these files could be added. The reason is next to each one.',
                         null, 0, failures);
         }
      } catch (err) {
         if (!(err instanceof UploadFailed)) err = new UploadFailed('Upload failed: ' + (err && err.message ? err.message : err));
         if (err.message === 'cancelled' || this.cancelled) {
            await this.discardCurrent();
            this.files.forEach(function (f) { forget(fingerprint(opts, f)); });
            if (opts.onCancelled) opts.onCancelled(this.replies.length);
         } else if (opts.onError) {
            opts.onError(err.message, err.reply, this.replies.length, failures);
         }
      } finally {
         window.removeEventListener('beforeunload', guard);
         document.removeEventListener('visibilitychange', wake);
         this.keepAwake(false);
      }
   };

   function start(files, opts) {
      var batch = new Batch(files, opts);
      batch.run();
      return {
         cancel: function () { batch.cancel(); },
         isFinishing: function () { return batch.finishing; }
      };
   }

   // ---- uploads that were cut off ----

   // Interrupted uploads into this collection, with how far each got. Gone ones are tidied away.
   function findResumable(opts) {
      var s = store();
      if (!s) return Promise.resolve([]);
      var prefix = STORE_PREFIX + opts.bundleId + ':' + opts.collectionId + ':';
      var found = [];
      for (var i = 0; i < s.length; i++) {
         var key = s.key(i);
         if (key && key.indexOf(prefix) === 0) found.push(key);
      }
      return Promise.all(found.map(function (key) {
         var url = recall(key);
         if (!url) { forget(key); return null; }
         var rest = key.slice(prefix.length);
         var parts = rest.split(':');
         // name:size:lastModified, and the name itself may hold colons.
         var name = parts.slice(0, -2).join(':');
         return request('HEAD', url, opts, {}).then(function (xhr) {
            if (xhr.status === 404 || xhr.status === 410) { forget(key); return null; }
            if (xhr.status !== 200) return null;
            return {
               key: key, url: url, name: name,
               offset: parseInt(xhr.getResponseHeader('Upload-Offset'), 10),
               length: parseInt(xhr.getResponseHeader('Upload-Length'), 10)
            };
         });
      })).then(function (rows) { return rows.filter(Boolean); });
   }

   // Right after files are picked: per file, what would stop it (or null), and how far an
   // earlier upload of it got (0 to 1, or null). The server applies its naming and size rules;
   // whether the bytes are NetCDF is read here. If the check can't reach the server, nothing is
   // held back: the upload asks again anyway.
   function precheck(files, opts) {
      var local = Promise.all(files.map(sniffFile));
      var remote = fetch(opts.checkUrl, {
         method: 'POST',
         credentials: 'same-origin',
         headers: { 'Content-Type': 'application/json', 'X-CSRFToken': opts.csrfToken,
                    'X-Requested-With': 'XMLHttpRequest' },
         body: JSON.stringify({ collection: opts.collectionId,
                                files: files.map(function (f) { return { name: f.name, size: f.size }; }) })
      }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
      var earlier = findResumable(opts).catch(function () { return []; });
      return Promise.all([local, remote, earlier]).then(function (all) {
         var kinds = all[0], server = all[1], entries = all[2];
         var answers = server && Array.isArray(server.files) && server.files.length === files.length ? server.files : null;
         return files.map(function (file, i) {
            var problem = !kinds[i] ? 'Not a NetCDF file.' : (answers && answers[i] && answers[i].problem) || null;
            var key = fingerprint(opts, file);
            var entry = entries.filter(function (e) { return e.key === key; })[0];
            var resume = entry && entry.length === file.size && entry.offset > 0 && entry.offset < entry.length
               ? entry.offset / entry.length : null;
            return { problem: problem, resume: problem ? null : resume };
         });
      });
   }

   function discard(entry, opts) {
      return request('DELETE', entry.url, opts, {}).then(function () { forget(entry.key); });
   }

   window.ElsaNetcdfUpload = {
      supported: supported,
      start: start,
      findResumable: findResumable,
      precheck: precheck,
      discard: discard,
      humanSize: humanSize,
      humanDuration: humanDuration,
      canCompress: canCompress
   };
})();
