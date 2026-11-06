/*
 * Admin Console page behaviour: the passkey step in front of admin mode, and the
 * extra tap some actions ask for.  Both reuse ElsaPasskeys (passkeys.js); the
 * server decides what a tap is for from the "purpose" in the verify URL.
 */
(function () {
    'use strict';

    var config = document.getElementById('staff-config');
    if (!config || !window.ElsaPasskeys) {
        return;
    }
    var csrf = config.getAttribute('data-csrf');

    function tap(purpose, next, scope) {
        var verify = config.getAttribute('data-verify') + '?purpose=' + encodeURIComponent(purpose);
        if (next) {
            verify += '&next=' + encodeURIComponent(next);
        }
        if (scope) {
            verify += '&for=' + encodeURIComponent(scope);
        }
        return window.ElsaPasskeys.authenticate({options: config.getAttribute('data-options'),
                                                 verify: verify}, csrf, false);
    }

    function showError(target, error) {
        if (target) {
            target.textContent = window.ElsaPasskeys.describe(error);
            target.classList.remove('d-none');
        }
    }

    // The verify page: one button opens admin mode.
    var enter = document.getElementById('staff-verify-button');
    if (enter) {
        enter.addEventListener('click', function () {
            var error = document.getElementById('staff-verify-error');
            if (!window.ElsaPasskeys.supported()) {
                showError(error, {name: 'NotSupportedError'});
                return;
            }
            enter.disabled = true;
            tap('enter', enter.getAttribute('data-next')).then(function (data) {
                window.location.assign(data.redirect || config.getAttribute('data-home'));
            }).catch(function (failure) {
                enter.disabled = false;
                showError(error, failure);
            });
        });
    }

    // Forms marked data-passkey-confirm send only after a fresh tap.
    Array.prototype.forEach.call(document.querySelectorAll('form[data-passkey-confirm]'), function (form) {
        form.addEventListener('submit', function (event) {
            if (form.getAttribute('data-confirmed') === '1') {
                return;
            }
            event.preventDefault();
            if (!form.reportValidity()) {
                return;
            }
            var button = form.querySelector('[type="submit"]');
            var error = form.querySelector('.staff-confirm-error');
            if (button) {
                button.disabled = true;
            }
            // The tap is good for this form's action only.
            var actionField = form.querySelector('[name="action"]');
            var scope = new URL(form.getAttribute('action') || window.location.href, window.location.href).pathname
                + '#' + (actionField ? actionField.value : '');
            tap('confirm', null, scope).then(function () {
                form.setAttribute('data-confirmed', '1');
                form.submit();
            }).catch(function (failure) {
                if (button) {
                    button.disabled = false;
                }
                showError(error, failure);
            });
        });
    });

    // Any form marked data-once cannot be sent twice by a double click.
    Array.prototype.forEach.call(document.querySelectorAll('form[data-once]'), function (form) {
        form.addEventListener('submit', function () {
            // After this tick, so the clicked button's own value still goes.
            window.setTimeout(function () {
                Array.prototype.forEach.call(form.querySelectorAll('[type="submit"]'), function (button) {
                    button.disabled = true;
                });
            }, 0);
        });
    });
})();

/*
 * Live filters.  A form marked data-live-filter="<id>" re-runs its search as
 * the admin types (after a short pause) or changes a dropdown, and swaps in
 * the element with that id from the fresh page.  The server does the
 * filtering, so paging and every filter behave exactly as a normal submit
 * would; without JavaScript the form still works with its button.
 */
(function () {
    'use strict';

    var PAUSE = 300;

    Array.prototype.forEach.call(document.querySelectorAll('form[data-live-filter]'), function (form) {
        var targetId = form.getAttribute('data-live-filter');
        var timer = null;
        var inflight = null;

        Array.prototype.forEach.call(form.querySelectorAll('[data-live-hide]'), function (element) {
            element.classList.add('d-none');
        });

        function run() {
            var params = new URLSearchParams();
            new FormData(form).forEach(function (value, key) {
                if (value !== '') {
                    params.append(key, value);
                }
            });
            var query = params.toString();
            var url = form.getAttribute('action') || window.location.pathname;
            url = url.split('?')[0] + (query ? '?' + query : '');

            if (inflight && inflight.abort) {
                inflight.abort();
            }
            inflight = window.AbortController ? new AbortController() : null;
            var box = document.getElementById(targetId);
            if (box) {
                box.classList.add('staff-live-busy');
                box.setAttribute('aria-busy', 'true');
            }

            fetch(url, {credentials: 'same-origin', signal: inflight ? inflight.signal : undefined,
                        headers: {'X-Requested-With': 'fetch'}})
                .then(function (response) {
                    // Admin mode ended, or anything unexpected: go there properly.
                    if (response.redirected || !response.ok) {
                        window.location.assign(url);
                        throw new Error('navigated');
                    }
                    return response.text();
                })
                .then(function (html) {
                    var fresh = new DOMParser().parseFromString(html, 'text/html').getElementById(targetId);
                    var current = document.getElementById(targetId);
                    if (fresh && current) {
                        current.replaceWith(document.importNode(fresh, true));
                    }
                    window.history.replaceState(null, '', url);
                    // Saving the view and exporting rows follow this filter.
                    Array.prototype.forEach.call(document.querySelectorAll('.staff-view-save input[name="query"]'), function (box) {
                        box.value = query;
                    });
                    Array.prototype.forEach.call(document.querySelectorAll('a[data-live-href]'), function (link) {
                        link.href = link.getAttribute('href').split('?')[0] + '?' + query;
                    });
                })
                .catch(function () {})
                .then(function () {
                    var current = document.getElementById(targetId);
                    if (current) {
                        current.classList.remove('staff-live-busy');
                        current.removeAttribute('aria-busy');
                    }
                });
        }

        form.addEventListener('input', function (event) {
            if (event.target.matches('input[type="search"], input[type="text"]')) {
                window.clearTimeout(timer);
                timer = window.setTimeout(run, PAUSE);
            }
        });
        form.addEventListener('change', function (event) {
            if (event.target.matches('select, input[type="checkbox"], input[type="radio"]')) {
                window.clearTimeout(timer);
                run();
            }
        });
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            window.clearTimeout(timer);
            run();
        });
    });
})();

/*
 * Admin mode countdown.  Two minutes before it ends, say so and offer to stay;
 * if the admin has typed recently, stay without asking.  The 8-hour limit
 * cannot be extended, so then the message only warns.  When it has ended,
 * offer the passkey step, which comes back to this page.
 */
(function () {
    'use strict';
    var bar = document.getElementById('staff-bar');
    var box = document.getElementById('staff-expiry');
    if (!bar || !box) {
        return;
    }
    var csrf = (document.getElementById('staff-config') || {}).getAttribute
        ? document.getElementById('staff-config').getAttribute('data-csrf') : '';
    var endsAt = Date.now() + 1000 * (parseInt(bar.getAttribute('data-seconds-left'), 10) || 0);
    var hard = bar.getAttribute('data-hard-limit') === '1';
    var lastInput = 0;
    var WARN = 120 * 1000;
    var text = document.getElementById('staff-expiry-text');
    var stayButton = document.getElementById('staff-expiry-stay');
    var verifyLink = document.getElementById('staff-expiry-verify');
    verifyLink.href = bar.getAttribute('data-verify') + '?next=' + encodeURIComponent(window.location.pathname + window.location.search);

    document.addEventListener('input', function () { lastInput = Date.now(); }, true);

    function stay() {
        return fetch(bar.getAttribute('data-stay'), {
            method: 'POST', credentials: 'same-origin',
            headers: {'X-CSRFToken': csrf, 'Accept': 'application/json'}
        }).then(function (response) {
            if (!response.ok) {
                throw new Error('ended');
            }
            return response.json();
        }).then(function (data) {
            endsAt = Date.now() + 1000 * data.seconds_left;
            hard = !!data.hard_limit;
            box.classList.add('d-none');
            var until = document.getElementById('staff-bar-until');
            if (until) {
                until.textContent = 'Admin mode until ' + new Date(endsAt).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
            }
        });
    }

    stayButton.addEventListener('click', function () {
        stay().catch(function () { endsAt = 0; tick(); });
    });

    function tick() {
        var left = endsAt - Date.now();
        if (left <= 0) {
            text.textContent = 'Admin mode has ended. Anything you were writing is kept in this browser; ' +
                'confirm with your passkey to carry on.';
            stayButton.classList.add('d-none');
            verifyLink.classList.remove('d-none');
            box.classList.remove('d-none');
            return;
        }
        if (left <= WARN) {
            if (!hard && Date.now() - lastInput < 5 * 60 * 1000) {
                stay().catch(function () {});
                return;
            }
            var minutes = Math.max(1, Math.round(left / 60000));
            text.textContent = hard
                ? 'Admin mode reaches its 8-hour limit in about ' + minutes + ' minute' + (minutes === 1 ? '' : 's') +
                  '. You will confirm with your passkey again; anything you are writing is kept.'
                : 'Admin mode ends in about ' + minutes + ' minute' + (minutes === 1 ? '' : 's') + ' unless you stay.';
            stayButton.classList.toggle('d-none', hard);
            box.classList.remove('d-none');
        }
    }
    window.setInterval(tick, 15000);
    tick();
})();

/*
 * Drafts.  Text typed in a box marked data-draft="<key>" is kept in this
 * browser until it is sent successfully, so a reload, a lost connection or
 * the passkey step never costs the admin what they wrote.  Drafts belong to
 * the person who wrote them, last a day, and are cleared on leaving admin
 * mode or signing out (base-derk.html), so a shared computer keeps nothing.
 */
(function () {
    'use strict';
    var PREFIX = 'elsa-draft:';
    var DAY = 24 * 3600 * 1000;
    var routes = document.getElementById('staff-routes');
    // Passkey page has no console around it. Keep drafts here.
    if (!routes) {
        return;
    }
    var me = routes.getAttribute('data-uid');
    var store;
    try {
        store = window.localStorage;
        store.getItem('x');
    } catch (e) {
        return;
    }

    // Drop drafts that are old or someone else's before anything is shown.
    try {
        Object.keys(store).forEach(function (k) {
            if (k.indexOf(PREFIX) !== 0) { return; }
            var value = null;
            try { value = JSON.parse(store.getItem(k) || 'null'); } catch (e) {}
            if (!value || Date.now() - value.at >= DAY || String(value.uid) !== String(me)) {
                store.removeItem(k);
            }
        });
    } catch (e) {}

    function read(key) {
        try {
            var value = JSON.parse(store.getItem(PREFIX + key) || 'null');
            if (value && Date.now() - value.at < DAY && String(value.uid) === String(me)) {
                return value.text;
            }
        } catch (e) {}
        return '';
    }

    // A send that worked shows a success message; only then is the draft gone.
    var sent = '';
    try {
        sent = window.sessionStorage.getItem('elsa-draft-sent') || '';
        window.sessionStorage.removeItem('elsa-draft-sent');
    } catch (e) {}
    if (sent && document.querySelector('.alert-success')) {
        sent.split('|').forEach(function (key) { try { store.removeItem(PREFIX + key); } catch (e) {} });
    }

    Array.prototype.forEach.call(document.querySelectorAll('[data-draft]'), function (field) {
        var key = field.getAttribute('data-draft');
        var saved = read(key);
        if (saved && !field.value) {
            field.value = saved;
            var note = document.createElement('div');
            note.className = 'staff-muted mt-1';
            note.textContent = 'Restored what you had written here.';
            field.parentNode.insertBefore(note, field.nextSibling);
        }
        var timer = null;
        field.addEventListener('input', function () {
            window.clearTimeout(timer);
            timer = window.setTimeout(function () {
                try {
                    if (field.value.trim()) {
                        store.setItem(PREFIX + key, JSON.stringify({text: field.value, at: Date.now(), uid: me}));
                    } else {
                        store.removeItem(PREFIX + key);
                    }
                } catch (e) {}
            }, 400);
        });
        if (field.form) {
            field.form.addEventListener('submit', function () {
                try {
                    // Save it now in case the send is refused.
                    window.clearTimeout(timer);
                    if (field.value.trim()) {
                        store.setItem(PREFIX + key, JSON.stringify({text: field.value, at: Date.now(), uid: me}));
                    }
                    var keys = Array.prototype.map.call(field.form.querySelectorAll('[data-draft]'),
                        function (f) { return f.getAttribute('data-draft'); });
                    window.sessionStorage.setItem('elsa-draft-sent', keys.join('|'));
                } catch (e) {}
            });
        }
    });

    var leave = document.getElementById('staff-leave-form');
    if (leave) {
        leave.addEventListener('submit', function () {
            try {
                Object.keys(store).forEach(function (k) { if (k.indexOf(PREFIX) === 0) { store.removeItem(k); } });
            } catch (e) {}
        });
    }
})();

/*
 * Saved replies inside the message windows: a picker marked
 * data-insert-into="#textarea" adds the chosen text to that box.
 */
(function () {
    'use strict';
    Array.prototype.forEach.call(document.querySelectorAll('select[data-insert-into]'), function (picker) {
        picker.addEventListener('change', function () {
            var target = document.querySelector(picker.getAttribute('data-insert-into'));
            var option = picker.options[picker.selectedIndex];
            var text = option && option.getAttribute('data-body');
            if (target && text) {
                target.value = target.value ? target.value.replace(/\s+$/, '') + '\n\n' + text : text;
                target.dispatchEvent(new Event('input', {bubbles: true}));
                target.focus();
            }
            picker.selectedIndex = 0;
        });
    });
})();

/*
 * Simple tabs: buttons with data-tab-target="#pane".  Without JavaScript
 * every pane stays visible, so nothing is ever out of reach.
 */
(function () {
    'use strict';
    var groups = document.querySelectorAll('.staff-tabs');
    Array.prototype.forEach.call(groups, function (group) {
        var tabs = group.querySelectorAll('[data-tab-target]');
        function show(active) {
            Array.prototype.forEach.call(tabs, function (tab) {
                var on = tab === active;
                tab.classList.toggle('active', on);
                tab.setAttribute('aria-selected', on ? 'true' : 'false');
                var pane = document.querySelector(tab.getAttribute('data-tab-target'));
                if (pane) {
                    pane.hidden = !on;
                }
            });
        }
        Array.prototype.forEach.call(tabs, function (tab) {
            tab.addEventListener('click', function () { show(tab); });
        });
        // Open on the side that has an unsent draft, else the first.
        var start = tabs[0];
        Array.prototype.forEach.call(tabs, function (tab) {
            var pane = document.querySelector(tab.getAttribute('data-tab-target'));
            var box = pane && pane.querySelector('textarea');
            if (box && box.value && start === tabs[0] && tab !== tabs[0]) {
                var first = document.querySelector(tabs[0].getAttribute('data-tab-target'));
                var firstBox = first && first.querySelector('textarea');
                if (!firstBox || !firstBox.value) {
                    start = tab;
                }
            }
        });
        if (start) {
            show(start);
        }
    });

    // A link to a box inside a tab opens the tab and puts the cursor there.
    document.addEventListener('click', function (event) {
        var link = event.target.closest ? event.target.closest('[data-focus]') : null;
        if (!link) {
            return;
        }
        var target = document.querySelector(link.getAttribute('data-focus'));
        if (!target) {
            return;
        }
        event.preventDefault();
        var pane = target.closest('[role="tabpanel"]');
        if (pane && pane.id) {
            var tab = document.querySelector('[data-tab-target="#' + pane.id + '"]');
            if (tab) {
                tab.click();
            }
        }
        target.scrollIntoView({block: 'center'});
        target.focus();
    });
})();

/*
 * Bulk actions in the queue.  Delegated, because the live filter replaces the
 * table: the bar follows whatever rows are on screen now.
 */
(function () {
    'use strict';
    function refresh() {
        var form = document.getElementById('bulk-form');
        if (!form) {
            return;
        }
        var picked = form.querySelectorAll('.bulk-pick:checked').length;
        var action = document.getElementById('bulk-action');
        var who = document.getElementById('bulk-assignee');
        var apply = document.getElementById('bulk-apply');
        var count = document.getElementById('bulk-count');
        if (!action) {
            return;
        }
        who.classList.toggle('d-none', action.value !== 'assign');
        apply.disabled = !picked || !action.value || (action.value === 'assign' && !who.value);
        count.textContent = picked ? picked + ' ticked' : 'Tick requests to act on several at once.';
    }
    document.addEventListener('change', function (event) {
        if (event.target.id === 'bulk-all') {
            Array.prototype.forEach.call(document.querySelectorAll('#bulk-form .bulk-pick'), function (box) {
                box.checked = event.target.checked;
            });
        }
        if (event.target.closest && event.target.closest('#bulk-form')) {
            refresh();
        }
    });
    refresh();
})();

/*
 * A checkbox marked data-tick-all ticks every row of its own table.
 */
(function () {
    'use strict';
    document.addEventListener('change', function (event) {
        if (event.target.matches && event.target.matches('[data-tick-all]')) {
            var table = event.target.closest('table');
            Array.prototype.forEach.call(table.querySelectorAll('tbody input[type="checkbox"]'), function (box) {
                box.checked = event.target.checked;
            });
        }
    });
})();

/*
 * Jump box: Ctrl+K or "/" focuses the search in the admin bar, which suggests
 * requests, users and bundles as you type.  Enter opens the highlighted one,
 * or the full search page.
 */
(function () {
    'use strict';
    var input = document.getElementById('staff-jump-input');
    var list = document.getElementById('staff-jump-list');
    if (!input || !list) {
        return;
    }
    var timer = null;
    var items = [];
    var active = -1;

    function close() {
        list.classList.add('d-none');
        input.setAttribute('aria-expanded', 'false');
        active = -1;
    }
    function render() {
        list.textContent = '';
        items.forEach(function (item, index) {
            var link = document.createElement('a');
            link.href = item.url;
            link.setAttribute('role', 'option');
            if (index === active) {
                link.className = 'active';
            }
            var kind = document.createElement('span');
            kind.className = 'staff-jump-kind';
            kind.textContent = item.kind;
            var label = document.createElement('span');
            label.textContent = item.label;
            link.appendChild(kind);
            link.appendChild(label);
            list.appendChild(link);
        });
        list.classList.toggle('d-none', !items.length);
        input.setAttribute('aria-expanded', items.length ? 'true' : 'false');
    }
    input.addEventListener('input', function () {
        window.clearTimeout(timer);
        var query = input.value.trim();
        if (!query) {
            items = [];
            close();
            return;
        }
        timer = window.setTimeout(function () {
            fetch(input.getAttribute('data-jump') + '?q=' + encodeURIComponent(query), {credentials: 'same-origin'})
                .then(function (response) { return response.ok ? response.json() : {results: []}; })
                .then(function (data) { items = data.results || []; active = items.length ? 0 : -1; render(); })
                .catch(function () {});
        }, 200);
    });
    input.addEventListener('keydown', function (event) {
        if (event.key === 'ArrowDown' && items.length) {
            event.preventDefault();
            active = (active + 1) % items.length;
            render();
        } else if (event.key === 'ArrowUp' && items.length) {
            event.preventDefault();
            active = (active - 1 + items.length) % items.length;
            render();
        } else if (event.key === 'Enter' && active >= 0 && items[active]) {
            event.preventDefault();
            window.location.assign(items[active].url);
        } else if (event.key === 'Escape') {
            close();
            input.blur();
        }
    });
    input.addEventListener('blur', function () { window.setTimeout(close, 150); });
})();

/*
 * Keyboard shortcuts.  Never while typing in a box, never with modifier keys
 * other than Ctrl+K.
 */
(function () {
    'use strict';
    var routes = document.getElementById('staff-routes');
    var input = document.getElementById('staff-jump-input');
    if (!routes) {
        return;
    }
    var pendingG = 0;
    function typing(target) {
        return target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
    }
    function showHelp() {
        var modal = document.getElementById('staffShortcuts');
        if (modal && window.bootstrap) {
            window.bootstrap.Modal.getOrCreateInstance(modal).show();
        }
    }
    var helpButton = document.getElementById('staff-shortcuts-open');
    if (helpButton) {
        helpButton.addEventListener('click', showHelp);
    }
    document.addEventListener('keydown', function (event) {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && input) {
            event.preventDefault();
            input.focus();
            input.select();
            return;
        }
        if (event.ctrlKey || event.metaKey || event.altKey || typing(event.target)) {
            return;
        }
        if (event.key === '/' && input) {
            event.preventDefault();
            input.focus();
            return;
        }
        if (event.key === '?') {
            showHelp();
            return;
        }
        if (pendingG && Date.now() - pendingG < 1500) {
            var target = {t: 'data-today', q: 'data-queue', p: 'data-pipeline', u: 'data-users', b: 'data-bundles'}[event.key];
            pendingG = 0;
            if (target) {
                window.location.assign(routes.getAttribute(target));
            }
            return;
        }
        if (event.key === 'g') {
            pendingG = Date.now();
            return;
        }
        var tab = {r: '#pane-reply', n: '#pane-note'}[event.key];
        if (tab) {
            var button = document.querySelector('[data-tab-target="' + tab + '"]');
            var pane = document.querySelector(tab);
            if (button && pane) {
                event.preventDefault();
                button.click();
                var box = pane.querySelector('textarea');
                if (box) {
                    box.focus();
                }
            }
        }
    });
})();

/*
 * Live updates on a case page: every 20 seconds (paused while the tab is
 * hidden) ask whether anything new happened and who else is looking.  The
 * check does not count as activity, so admin mode still ends when nobody
 * works.  Stops for good once admin mode has ended.
 */
(function () {
    'use strict';
    var live = document.getElementById('staff-live');
    if (!live) {
        return;
    }
    var presence = document.getElementById('staff-presence');
    var stopped = false;
    function check() {
        if (stopped || document.hidden) {
            return;
        }
        fetch(live.getAttribute('data-pulse') + '?since=' + encodeURIComponent(live.getAttribute('data-since')),
              {credentials: 'same-origin', headers: {'Accept': 'application/json'}})
            .then(function (response) {
                if (!response.ok) {
                    stopped = true;
                    throw new Error('stopped');
                }
                return response.json();
            })
            .then(function (data) {
                if (data.new) {
                    document.getElementById('staff-live-text').textContent = data.from_user
                        ? 'The user answered while you had this open.'
                        : 'A colleague added to this request while you had it open.';
                    live.classList.remove('d-none');
                }
                if (presence) {
                    presence.textContent = data.viewers && data.viewers.length
                        ? '· ' + data.viewers.join(', ') + (data.viewers.length === 1 ? ' is' : ' are') + ' viewing this too'
                        : '';
                }
            })
            .catch(function () {});
    }
    window.setInterval(check, 20000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) { check(); } });
    check();
})();

/*
 * After an action the page comes back at the section being worked on (an
 * #anchor), below the messages at the top.  Repeat the message there, so
 * "Reply sent" or an error is seen where the person is looking.
 */
(function () {
    'use strict';
    var box = document.getElementById('staff-messages');
    if (!box || !window.location.hash || window.location.hash.length < 2) {
        return;
    }
    var target;
    try {
        target = document.querySelector(window.location.hash);
    } catch (e) {
        return;
    }
    if (!target) {
        return;
    }
    var copy = box.cloneNode(true);
    copy.id = 'staff-messages-here';
    copy.className = 'mt-2 mb-2';
    var head = target.closest('.staff-card-head');
    if (head) {
        head.parentNode.insertBefore(copy, head.nextSibling);
    } else {
        target.parentNode.insertBefore(copy, target);
    }
    box.classList.add('d-none');
    copy.scrollIntoView({block: 'center'});
})();


/*
 * Confirmations ("Note added.") close themselves after a few seconds, a
 * little longer for longer ones.  Warnings and errors stay until closed:
 * they ask the reader to do something.  The countdown waits while the
 * pointer is over a message or focus is inside it, and a closed message's
 * box goes too, so no gap is left.  Runs after the block above, so the copy
 * it places beside the section being worked on is handled the same way.
 */
(function () {
    'use strict';
    var BASE = 5000;
    var PER_CHARACTER = 40;
    var LONGEST = 12000;
    var FADE = 200;
    var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function tidy(box) {
        if (box && (box.id === 'staff-messages' || box.id === 'staff-messages-here')
                && !box.querySelector('.alert')) {
            box.parentNode.removeChild(box);
        }
    }

    function close(alert) {
        if (!alert.parentNode) {
            return;
        }
        var box = alert.parentNode;
        alert.classList.remove('show');
        window.setTimeout(function () {
            if (alert.parentNode) {
                alert.parentNode.removeChild(alert);
            }
            tidy(box);
        }, still ? 0 : FADE);
    }

    function arm(alert) {
        var wait = Math.min(LONGEST, BASE + PER_CHARACTER * (alert.textContent || '').trim().length);
        var timer = null;
        var paused = false;
        function start() {
            if (!paused && timer === null) {
                timer = window.setTimeout(function () { close(alert); }, wait);
            }
        }
        function pause() {
            paused = true;
            window.clearTimeout(timer);
            timer = null;
        }
        function resume() {
            paused = alert.matches(':hover') || alert.contains(document.activeElement);
            start();
        }
        alert.addEventListener('mouseenter', pause);
        alert.addEventListener('focusin', pause);
        alert.addEventListener('mouseleave', resume);
        alert.addEventListener('focusout', function () { window.setTimeout(resume, 0); });
        start();
    }

    Array.prototype.forEach.call(document.querySelectorAll('.alert[data-autoclose]'), arm);

    // Closed with the X. Bootstrap removes the message after the fade, then tidy the box.
    document.addEventListener('close.bs.alert', function (event) {
        var box = event.target && event.target.parentNode;
        window.setTimeout(function () { tidy(box); }, still ? 50 : FADE + 300);
    });
})();
