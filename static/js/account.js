/* The account page: each setting's modal posts to a JSON endpoint and the page
   updates in place.  Server responses are {ok: true, ...} or
   {ok: false, errors: {field: [message, ...], __all__: [...]}}. */
(function () {
    'use strict';

    var page = document.getElementById('account-page');
    if (!page) { return; }

    var csrfInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
    var csrf = csrfInput ? csrfInput.value : '';

    // ---- small helpers -------------------------------------------------------------

    function $(selector, root) { return (root || document).querySelector(selector); }
    function $$(selector, root) { return Array.prototype.slice.call((root || document).querySelectorAll(selector)); }

    function modalFor(element) {
        var el = element.closest('.modal');
        return el ? bootstrap.Modal.getOrCreateInstance(el) : null;
    }

    function setText(key, value) {
        $$('[data-account="' + key + '"]').forEach(function (el) { el.textContent = value; });
    }

    // The row whose Edit button opens this modal briefly shows that it saved.
    function flashRow(modalId) {
        $$('.account-row').forEach(function (row) {
            if (!row.querySelector('[data-bs-target="#' + modalId + '"]')) { return; }
            var label = row.querySelector('.account-row-label');
            row.classList.remove('account-saved');
            void row.offsetWidth;
            row.classList.add('account-saved');
            if (label && !label.querySelector('.account-saved-tick')) {
                var tick = document.createElement('span');
                tick.className = 'account-saved-tick';
                tick.innerHTML = '<i class="bi bi-check2" aria-hidden="true"></i> Saved';
                label.appendChild(tick);
                setTimeout(function () { tick.remove(); }, 2400);
            }
        });
    }

    var toastBox = document.getElementById('account-toasts');

    function toast(message, kind) {
        var el = document.createElement('div');
        el.className = 'toast align-items-center border-0 shadow-sm text-white ' +
            (kind === 'error' ? 'bg-danger' : 'account-btn-primary');
        el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
        el.innerHTML = '<div class="d-flex"><div class="toast-body"></div>' +
            '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button></div>';
        el.querySelector('.toast-body').textContent = message;
        toastBox.appendChild(el);
        el.addEventListener('hidden.bs.toast', function () { el.remove(); });
        new bootstrap.Toast(el, { delay: 4500 }).show();
    }

    function post(url, data) {
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
            body: data || new FormData()
        }).then(function (response) {
            var type = response.headers.get('Content-Type') || '';
            if (type.indexOf('application/json') === -1) {
                // A redirect to the landing page means the session ended.
                return { ok: false, errors: { __all__: ['Your session has ended. Reload the page and sign in again.'] } };
            }
            return response.json();
        }, function () {
            return { ok: false, errors: { __all__: ['ELSA could not be reached. Check your connection and try again.'] } };
        });
    }

    // ---- errors and busy state --------------------------------------------------------

    function clearErrors(form) {
        $$('.is-invalid', form).forEach(function (el) {
            el.classList.remove('is-invalid');
            el.removeAttribute('aria-invalid');
        });
        $$('[data-error-for]', form).forEach(function (el) {
            el.textContent = '';
            if (el.classList.contains('alert')) { el.classList.add('d-none'); }
        });
    }

    function showErrors(form, errors) {
        var firstInvalid = null;
        Object.keys(errors || {}).forEach(function (field) {
            var text = errors[field].join(' ');
            var input = field === '__all__' ? null : form.querySelector('[name="' + field + '"]');
            var slot = form.querySelector('[data-error-for="' + field + '"]');
            if (input && slot) {
                input.classList.add('is-invalid');
                input.setAttribute('aria-invalid', 'true');
                slot.textContent = text;
                firstInvalid = firstInvalid || input;
            } else {
                var general = form.querySelector('[data-error-for="__all__"]');
                if (general) {
                    general.textContent = (general.textContent ? general.textContent + ' ' : '') + text;
                    general.classList.remove('d-none');
                } else {
                    toast(text, 'error');
                }
            }
        });
        if (firstInvalid) { firstInvalid.focus(); }
    }

    function setBusy(form, busy) {
        var button = form.querySelector('[data-submit]');
        if (!button) { return; }
        button.disabled = busy || button.dataset.blocked === 'true';
        var spinner = button.querySelector('.spinner-border');
        if (spinner) { spinner.classList.toggle('d-none', !busy); }
        form.setAttribute('aria-busy', busy ? 'true' : 'false');
    }

    function blockSubmit(form, blocked) {
        var button = form.querySelector('[data-submit]');
        if (!button) { return; }
        button.dataset.blocked = blocked ? 'true' : 'false';
        button.disabled = blocked;
    }

    function submitTo(form, url, onSuccess) {
        clearErrors(form);
        setBusy(form, true);
        return post(url, new FormData(form)).then(function (data) {
            setBusy(form, false);
            if (data.ok) {
                var owner = form.closest('.modal');
                if (owner) { owner.dataset.saved = 'true'; }
                onSuccess(data);
            } else {
                showErrors(form, data.errors);
            }
            return data;
        });
    }

    // Each modal starts clean every time it opens.
    $$('.modal').forEach(function (modalEl) {
        modalEl.addEventListener('hidden.bs.modal', function () {
            $$('form.account-form', modalEl).forEach(function (form) {
                form.reset();
                clearErrors(form);
                setBusy(form, false);
                form.dispatchEvent(new Event('account:reset'));
            });
        });
        modalEl.addEventListener('shown.bs.modal', function () {
            var visible = $$('form.account-form', modalEl).filter(function (f) { return !f.classList.contains('d-none'); })[0];
            var first = visible && $$('input:not([type=hidden]):not([tabindex="-1"])', visible)
                .filter(function (i) { return !i.disabled; })[0];
            if (first) { first.focus(); first.select && first.select(); }
        });
    });

    // ---- name -------------------------------------------------------------------------

    (function () {
        var form = $('#nameModal form');
        var inputs = $$('input', form);

        function initial(input) { return input.getAttribute('value') || ''; }
        function refresh() {
            var changed = inputs.some(function (i) { return i.value.trim() !== initial(i); });
            var filled = inputs.every(function (i) { return i.value.trim() !== ''; });
            blockSubmit(form, !(changed && filled));
        }
        inputs.forEach(function (i) { i.addEventListener('input', refresh); });
        form.addEventListener('account:reset', refresh);
        refresh();

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            submitTo(form, form.dataset.endpoint, function (data) {
                inputs[0].setAttribute('value', data.first_name);
                inputs[1].setAttribute('value', data.last_name);
                setText('full_name', data.full_name);
                modalFor(form).hide();
                flashRow('nameModal');
                toast(data.message);
            });
        });
    })();

    // ---- email: two steps in one modal -------------------------------------------------

    (function () {
        var modalEl = document.getElementById('emailModal');
        var addressForm = $('form[data-step="address"]', modalEl);
        var codeForm = $('form[data-step="code"]', modalEl);
        var codeInput = $('#email-code');
        var resend = $('#email-resend');
        var restart = $('#email-restart');
        var banner = document.getElementById('email-pending');
        var pending = modalEl.dataset.pending || '';
        var timer = null;

        function showStep(step) {
            addressForm.classList.toggle('d-none', step !== 'address');
            codeForm.classList.toggle('d-none', step !== 'code');
        }

        function setPending(address) {
            pending = address || '';
            setText('pending_email', pending);
            banner.classList.toggle('d-none', !pending);
        }

        function countdown(seconds) {
            clearInterval(timer);
            var left = parseInt(seconds, 10) || 0;
            function tick() {
                if (left > 0) {
                    resend.disabled = true;
                    resend.textContent = 'Send a new code (' + left + 's)';
                    left -= 1;
                } else {
                    clearInterval(timer);
                    resend.disabled = false;
                    resend.textContent = 'Send a new code';
                }
            }
            tick();
            timer = setInterval(tick, 1000);
        }

        modalEl.addEventListener('show.bs.modal', function () {
            showStep(pending ? 'code' : 'address');
        });

        addressForm.addEventListener('submit', function (event) {
            event.preventDefault();
            submitTo(addressForm, addressForm.dataset.endpoint, function (data) {
                setPending(data.pending_email);
                showStep('code');
                countdown(data.resend_wait);
                codeInput.focus();
            });
        });

        codeForm.addEventListener('submit', function (event) {
            event.preventDefault();
            submitTo(codeForm, codeForm.dataset.endpoint, function (data) {
                setText('email', data.email);
                setPending('');
                modalFor(codeForm).hide();
                flashRow('emailModal');
                toast(data.message);
            }).then(function (data) {
                if (!data.ok && (data.errors.__all__ || []).length && !data.errors.code) {
                    // The change itself is gone (too many guesses, address taken).
                    setPending('');
                }
            });
        });

        // Digits only, and submit as soon as all six are in.
        codeInput.addEventListener('input', function () {
            var digits = codeInput.value.replace(/\D/g, '').slice(0, 6);
            if (digits !== codeInput.value) { codeInput.value = digits; }
            if (digits.length === 6 && codeForm.getAttribute('aria-busy') !== 'true') {
                codeForm.requestSubmit ? codeForm.requestSubmit() : codeForm.dispatchEvent(new Event('submit', { cancelable: true }));
            }
        });

        resend.addEventListener('click', function () {
            clearErrors(codeForm);
            resend.disabled = true;
            post(resend.dataset.endpoint).then(function (data) {
                if (data.ok) {
                    countdown(data.resend_wait);
                    toast(data.message);
                    codeInput.focus();
                } else {
                    resend.disabled = false;
                    showErrors(codeForm, data.errors);
                }
            });
        });

        function cancelChange() {
            return post(restart.dataset.endpoint).then(function (data) {
                if (data.ok) { setPending(''); }
                return data;
            });
        }

        restart.addEventListener('click', function () {
            cancelChange().then(function () {
                clearErrors(codeForm);
                showStep('address');
                $('#email-new').focus();
            });
        });

        document.getElementById('email-pending-cancel').addEventListener('click', function () {
            cancelChange().then(function (data) {
                if (data.ok) { toast('Email change cancelled.'); }
            });
        });

        if (pending) { countdown(modalEl.dataset.resendWait); }
    })();

    // ---- password -----------------------------------------------------------------------

    (function () {
        var form = $('#passwordModal form');
        var oldInput = $('#pw-old');
        var newInput = $('#pw-new');
        var confirmInput = $('#pw-confirm');
        var rules = $('#pw-rules');
        var hint = $('#pw-confirm-hint');
        var minLength = parseInt(rules.dataset.minLength, 10) || 8;

        function mark(rule, met, touched) {
            var li = rules.querySelector('[data-rule="' + rule + '"]');
            var icon = li.querySelector('i');
            li.classList.toggle('met', touched && met);
            li.classList.toggle('unmet', touched && !met);
            icon.className = 'bi ' + (!touched ? 'bi-circle' : met ? 'bi-check-circle-fill' : 'bi-x-circle');
        }

        function refresh() {
            var value = newInput.value;
            var touched = value.length > 0;
            mark('length', value.length >= minLength, touched);
            mark('numeric', !/^\d+$/.test(value), touched);

            if (confirmInput.value) {
                var same = confirmInput.value === value;
                hint.textContent = same ? 'Passwords match.' : 'Passwords do not match yet.';
                hint.className = 'form-text ' + (same ? 'text-success' : 'text-danger');
            } else {
                hint.textContent = '';
            }

            blockSubmit(form, !(oldInput.value && value && confirmInput.value));
        }

        [oldInput, newInput, confirmInput].forEach(function (i) { i.addEventListener('input', refresh); });
        form.addEventListener('account:reset', function () {
            $$('[data-reveal]', form).forEach(function (button) { reveal(button, false); });
            refresh();
        });
        refresh();

        function reveal(button, show) {
            var input = document.getElementById(button.dataset.reveal);
            input.type = show ? 'text' : 'password';
            button.setAttribute('aria-pressed', show ? 'true' : 'false');
            button.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
            button.querySelector('i').className = 'bi ' + (show ? 'bi-eye-slash' : 'bi-eye');
        }
        $$('[data-reveal]', form).forEach(function (button) {
            button.addEventListener('click', function () {
                reveal(button, button.getAttribute('aria-pressed') !== 'true');
            });
        });

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            submitTo(form, form.dataset.endpoint, function (data) {
                setText('password_changed', data.changed_label);
                modalFor(form).hide();
                flashRow('passwordModal');
                toast(data.message);
            });
        });
    })();

    // ---- username ------------------------------------------------------------------------

    (function () {
        var form = $('#usernameModal form');
        var input = $('#username-new');
        var password = $('#username-password');

        function current() { return input.getAttribute('value') || ''; }
        function refresh() {
            var value = input.value.trim();
            blockSubmit(form, !(value && value !== current() && password.value));
        }
        [input, password].forEach(function (i) { i.addEventListener('input', refresh); });
        form.addEventListener('account:reset', refresh);
        refresh();

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            submitTo(form, form.dataset.endpoint, function (data) {
                input.setAttribute('value', data.username);
                setText('username', data.username);
                $$('input[name="username"][autocomplete="username"]').forEach(function (i) { i.value = data.username; });
                $('#close-confirm').dataset.expected = data.username;
                modalFor(form).hide();
                flashRow('usernameModal');
                toast(data.message);
            });
        });
    })();

    // ---- agency: describe the request, then review an AI draft and send ----------------------

    (function () {
        var modalEl = document.getElementById('agencyModal');
        var askForm = $('form[data-step="ask"]', modalEl);
        var reviewForm = $('form[data-step="review"]', modalEl);
        var note = $('#agency-draft-note');

        function showStep(step) {
            askForm.classList.toggle('d-none', step !== 'ask');
            reviewForm.classList.toggle('d-none', step !== 'review');
        }

        function requestDraft() {
            return submitTo(askForm, askForm.dataset.endpoint, function (data) {
                reviewForm.elements.agency.value = askForm.elements.agency.value;
                reviewForm.elements.note.value = askForm.elements.note.value;
                reviewForm.elements.subject.value = data.subject;
                reviewForm.elements.body.value = data.body;
                note.textContent = data.source === 'ai'
                    ? 'Drafted with AI. Read it over and change anything before you send it.'
                    : 'Here is a draft. Read it over and change anything before you send it.';
                clearErrors(reviewForm);
                showStep('review');
                reviewForm.elements.body.focus();
                reviewForm.elements.body.setSelectionRange(0, 0);
                reviewForm.elements.body.scrollTop = 0;
            });
        }

        askForm.addEventListener('submit', function (event) {
            event.preventDefault();
            requestDraft();
        });

        $('#agency-back').addEventListener('click', function () {
            showStep('ask');
            askForm.elements.note.focus();
        });

        $('#agency-redraft').addEventListener('click', function () {
            var button = this;
            button.disabled = true;
            setBusy(reviewForm, true);
            requestDraft().then(function () {
                button.disabled = false;
                setBusy(reviewForm, false);
            });
        });

        reviewForm.addEventListener('submit', function (event) {
            event.preventDefault();
            submitTo(reviewForm, reviewForm.dataset.endpoint, function (data) {
                modalFor(reviewForm).hide();
                toast(data.message);
            });
        });

        modalEl.addEventListener('hidden.bs.modal', function () {
            note.textContent = '';
            showStep('ask');
        });
    })();

    // ---- passkeys: add (named first) and rename -------------------------------------------

    (function () {
        var modalEl = document.getElementById('passkeyNameModal');
        var form = $('form', modalEl);
        var title = $('#passkeyNameModalLabel');
        var nameInput = $('#passkey-name');
        var label = $('[data-submit-label]', form);
        var addOnly = $('[data-add-only]', form);
        var addButton = document.getElementById('add-passkey-btn');
        var alertBox = document.getElementById('passkey-alert');
        var mode = 'add';
        var target = null;

        var supported = window.ElsaPasskeys && window.ElsaPasskeys.supported();
        if (!supported) {
            addButton.disabled = true;
            alertBox.className = 'alert alert-warning py-2 px-3 mt-3 mb-0 small';
            alertBox.textContent = 'This browser does not support passkeys. Try a current version of ' +
                                   'Chrome, Safari, Edge, or Firefox.';
        }

        function suggestedName() {
            var ua = navigator.userAgent;
            var device = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) ? 'iPad' : /Android/.test(ua) ? 'Android phone'
                : /Mac/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows PC' : /Linux/.test(ua) ? 'Linux computer' : 'device';
            return 'My ' + device;
        }

        modalEl.addEventListener('show.bs.modal', function (event) {
            var trigger = event.relatedTarget;
            mode = trigger && trigger.dataset.mode === 'rename' ? 'rename' : 'add';
            target = trigger;
            title.textContent = mode === 'rename' ? 'Rename passkey' : 'Add a passkey';
            label.textContent = mode === 'rename' ? 'Save' : 'Continue';
            addOnly.classList.toggle('d-none', mode === 'rename');
            nameInput.value = mode === 'rename' ? trigger.dataset.passkeyName : suggestedName();
            nameInput.defaultValue = nameInput.value;
        });

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            clearErrors(form);
            var name = nameInput.value.trim();
            if (!name) {
                showErrors(form, { name: ['Give the passkey a name.'] });
                return;
            }

            if (mode === 'rename') {
                submitTo(form, target.dataset.url, function (data) {
                    var row = document.getElementById('passkey-' + target.dataset.passkeyId);
                    $('[data-passkey-name]', row).textContent = data.name;
                    $$('button[data-passkey-name]', row).forEach(function (b) { b.dataset.passkeyName = data.name; });
                    modalFor(form).hide();
                    toast(data.message);
                });
                return;
            }

            setBusy(form, true);
            window.ElsaPasskeys.register({
                options: form.dataset.registerOptions,
                verify: form.dataset.registerVerify
            }, csrf, name).then(function () {
                // Reload so the new key appears with its real dates.
                window.location.reload();
            }).catch(function (error) {
                setBusy(form, false);
                showErrors(form, { __all__: [window.ElsaPasskeys.describe(error)] });
            });
        });
    })();

    // One remove modal reused for every passkey.
    (function () {
        var modalEl = document.getElementById('deletePasskeyModal');
        modalEl.addEventListener('show.bs.modal', function (event) {
            var button = event.relatedTarget;
            if (!button) { return; }
            document.getElementById('delete-passkey-name').textContent = button.dataset.passkeyName;
            document.getElementById('delete-passkey-form').action = button.dataset.passkeyUrl;
        });
    })();

    // ---- close account ----------------------------------------------------------------------

    (function () {
        var form = $('#closeAccountModal form');
        var password = $('#close-password');
        var confirmInput = $('#close-confirm');

        function refresh() {
            blockSubmit(form, !(password.value && confirmInput.value.trim() === confirmInput.dataset.expected));
        }
        [password, confirmInput].forEach(function (i) { i.addEventListener('input', refresh); });
        form.addEventListener('account:reset', refresh);
        refresh();

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            submitTo(form, form.dataset.endpoint, function (data) {
                window.location.assign(data.redirect);
            });
        });
    })();

    // ---- researcher details --------------------------------------------------------------

    (function () {
        var form = $('#researchModal form');
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            submitTo(form, form.dataset.endpoint, function (data) {
                var orcid = $('[data-account="orcid"]');
                orcid.textContent = '';
                if (data.orcid) {
                    var link = document.createElement('a');
                    link.className = 'account-link';
                    link.href = 'https://orcid.org/' + data.orcid;
                    link.target = '_blank';
                    link.rel = 'noopener';
                    link.textContent = data.orcid;
                    orcid.appendChild(link);
                } else {
                    orcid.innerHTML = '<span class="text-muted">Not added</span>';
                }
                var affiliation = $('[data-account="affiliation"]');
                if (data.affiliation) {
                    affiliation.textContent = data.affiliation;
                } else {
                    affiliation.innerHTML = '<span class="text-muted">Not added</span>';
                }
                $('[data-account="research_button"]').textContent = (data.orcid || data.affiliation) ? 'Edit' : 'Add';
                form.elements.orcid.setAttribute('value', data.orcid);
                form.elements.affiliation.setAttribute('value', data.affiliation);
                modalFor(form).hide();
                flashRow('researchModal');
                toast(data.message);
            });
        });
    })();

    // ---- devices and remembered devices ---------------------------------------------------------

    (function () {
        function removeAfterPost(button, itemSelector) {
            button.disabled = true;
            post(button.dataset.endpoint).then(function (data) {
                if (data.ok) {
                    var item = button.closest(itemSelector);
                    if (item) { item.remove(); }
                    toast(data.message);
                } else {
                    button.disabled = false;
                    toast((data.errors.__all__ || ['That did not work.'])[0], 'error');
                }
            });
        }

        $$('[data-end-session]').forEach(function (button) {
            button.addEventListener('click', function () { removeAfterPost(button, '.account-device'); });
        });
        $$('[data-forget-device]').forEach(function (button) {
            button.addEventListener('click', function () { removeAfterPost(button, '.account-device'); });
        });

        var everywhere = document.getElementById('end-other-sessions');
        if (everywhere) {
            everywhere.addEventListener('click', function () {
                everywhere.disabled = true;
                post(everywhere.dataset.endpoint).then(function (data) {
                    if (data.ok) {
                        $$('[data-end-session]').forEach(function (b) { b.closest('.account-device').remove(); });
                        $$('[data-forget-device]').forEach(function (b) {
                            var item = b.closest('.account-device');
                            if (!item.querySelector('.account-badge')) { item.remove(); }
                        });
                        everywhere.remove();
                        toast(data.message);
                    } else {
                        everywhere.disabled = false;
                        toast((data.errors.__all__ || ['That did not work.'])[0], 'error');
                    }
                });
            });
        }

        var more = document.getElementById('events-more');
        if (more) {
            more.addEventListener('click', function () {
                $$('.account-event-more').forEach(function (li) { li.classList.remove('d-none'); });
                more.remove();
            });
        }
    })();

    // ---- unsaved changes ------------------------------------------------------------------------
    // Closing a modal with edits in it asks first, inside the modal (never a
    // browser confirm()): a stray Esc or backdrop click should not eat a draft.

    (function () {
        function isDirty(modalEl) {
            return $$('form.account-form:not(.d-none)', modalEl).some(function (form) {
                return $$('input, textarea, select', form).some(function (field) {
                    if (field.type === 'hidden' || field.readOnly || field.disabled || field.tabIndex === -1) { return false; }
                    if (field.tagName === 'SELECT') {
                        return Array.prototype.some.call(field.options, function (o) { return o.selected !== o.defaultSelected; });
                    }
                    if (field.type === 'checkbox' || field.type === 'radio') { return field.checked !== field.defaultChecked; }
                    return field.value !== field.defaultValue;
                });
            });
        }

        $$('.modal').forEach(function (modalEl) {
            if (!modalEl.querySelector('form.account-form')) { return; }

            modalEl.addEventListener('hide.bs.modal', function (event) {
                if (modalEl.dataset.saved === 'true' || modalEl.dataset.discard === 'true' || !isDirty(modalEl)) { return; }
                event.preventDefault();
                var footer = $$('.modal-footer', modalEl).filter(function (f) { return f.offsetParent !== null; })[0];
                if (!footer || footer.querySelector('.account-discard')) { return; }
                var bar = document.createElement('div');
                bar.className = 'account-discard';
                bar.setAttribute('role', 'alert');
                bar.innerHTML = '<span>Discard your changes?</span><span class="d-flex gap-2">' +
                    '<button type="button" class="btn btn-sm btn-outline-secondary" data-keep>Keep editing</button>' +
                    '<button type="button" class="btn btn-sm btn-danger btn-keep-bg text-white" data-discard>Discard</button></span>';
                footer.insertBefore(bar, footer.firstChild);
                bar.querySelector('[data-keep]').addEventListener('click', function () { bar.remove(); });
                bar.querySelector('[data-discard]').addEventListener('click', function () {
                    modalEl.dataset.discard = 'true';
                    bootstrap.Modal.getOrCreateInstance(modalEl).hide();
                });
                bar.querySelector('[data-keep]').focus();
            });

            modalEl.addEventListener('hidden.bs.modal', function () {
                delete modalEl.dataset.saved;
                delete modalEl.dataset.discard;
                $$('.account-discard', modalEl).forEach(function (bar) { bar.remove(); });
            });
        });
    })();

    // ---- open a modal named in the address, e.g. ?edit=email ----------------------------------

    var targets = { name: 'nameModal', email: 'emailModal', username: 'usernameModal', password: 'passwordModal',
                    agency: 'agencyModal', research: 'researchModal', close: 'closeAccountModal' };
    var wanted = targets[page.dataset.openModal];
    if (wanted) {
        bootstrap.Modal.getOrCreateInstance(document.getElementById(wanted)).show();
        if (window.history.replaceState) {
            window.history.replaceState(null, '', window.location.pathname);
        }
    }
})();
