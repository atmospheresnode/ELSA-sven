/*
 * ELSA passkey (WebAuthn) helpers.
 *
 * The browser API deals in ArrayBuffers while the server speaks base64url
 * JSON, so most of this file is translation between the two.  Both ceremonies
 * are two round trips: fetch options carrying a challenge, hand them to the
 * authenticator, post the signed result back.
 */
(function (global) {
    'use strict';

    function base64urlToBuffer(value) {
        var padding = '='.repeat((4 - (value.length % 4)) % 4);
        var base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/');
        var raw = global.atob(base64);
        var bytes = new Uint8Array(raw.length);
        for (var i = 0; i < raw.length; i++) {
            bytes[i] = raw.charCodeAt(i);
        }
        return bytes.buffer;
    }

    function bufferToBase64url(buffer) {
        var bytes = new Uint8Array(buffer);
        var binary = '';
        for (var i = 0; i < bytes.length; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return global.btoa(binary)
            .replace(/\+/g, '-')
            .replace(/\//g, '_')
            .replace(/=+$/, '');
    }

    function post(url, body, csrfToken) {
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify(body || {})
        }).then(function (response) {
            return response.json().catch(function () {
                return {error: 'The server sent an unreadable response.'};
            }).then(function (data) {
                if (!response.ok) {
                    var failure = new Error(data.error || 'Something went wrong. Please try again.');
                    failure.reason = data.reason;
                    failure.credentialId = data.credential_id;
                    throw failure;
                }
                return data;
            });
        });
    }

    /* Errors from the authenticator are technical and sometimes alarming.
       Translate the ones users actually hit. */
    function describe(error) {
        if (!error) {
            return 'Something went wrong. Please try again.';
        }
        if (error.name === 'NotAllowedError') {
            return 'The request was cancelled or timed out. Please try again.';
        }
        if (error.name === 'InvalidStateError') {
            return 'This device already has a passkey for ELSA.';
        }
        if (error.name === 'SecurityError') {
            return 'Passkeys need a secure connection to this site.';
        }
        if (error.name === 'AbortError') {
            return 'The request was cancelled.';
        }
        if (error.reason === 'unknown_credential') {
            return 'That passkey is no longer registered with ELSA. It was probably '
                 + 'removed from your account. Sign in with your password, then set '
                 + 'up a new one.';
        }
        return error.message || 'Something went wrong. Please try again.';
    }

    function supported() {
        return !!(global.PublicKeyCredential && global.navigator && global.navigator.credentials);
    }

    /*
     * Removing a passkey from ELSA cannot reach into the authenticator holding
     * it, so the browser carries on offering a credential the server no longer
     * knows.  This tells the passkey provider to drop it, which is the only way
     * to stop it reappearing in the picker.  Newer browsers only, so it is
     * best effort.
     */
    function forget(credentialId) {
        if (!credentialId || !global.PublicKeyCredential ||
                !global.PublicKeyCredential.signalUnknownCredential) {
            return Promise.resolve();
        }
        return global.PublicKeyCredential.signalUnknownCredential({
            rpId: global.location.hostname,
            credentialId: credentialId
        }).catch(function () {});
    }

    /* Autofill-driven passkeys are newer than passkeys themselves, so this is
       a separate check rather than part of supported(). */
    function autofillSupported() {
        if (!supported() || !global.PublicKeyCredential.isConditionalMediationAvailable) {
            return Promise.resolve(false);
        }
        return global.PublicKeyCredential.isConditionalMediationAvailable()
            .catch(function () { return false; });
    }

    function register(urls, csrfToken, label) {
        return post(urls.options, {}, csrfToken).then(function (options) {
            options.challenge = base64urlToBuffer(options.challenge);
            options.user.id = base64urlToBuffer(options.user.id);
            (options.excludeCredentials || []).forEach(function (credential) {
                credential.id = base64urlToBuffer(credential.id);
            });

            return global.navigator.credentials.create({publicKey: options});
        }).then(function (credential) {
            var response = credential.response;
            return post(urls.verify, {
                name: label,
                credential: {
                    id: credential.id,
                    rawId: bufferToBase64url(credential.rawId),
                    type: credential.type,
                    clientExtensionResults: credential.getClientExtensionResults(),
                    response: {
                        clientDataJSON: bufferToBase64url(response.clientDataJSON),
                        attestationObject: bufferToBase64url(response.attestationObject),
                        transports: response.getTransports ? response.getTransports() : []
                    }
                }
            }, csrfToken);
        });
    }

    /*
     * conditional: when true the browser does not put up a modal.  It offers
     * the passkey inside the username field's autofill list instead, and stays
     * silent if there is nothing to offer.  That makes it safe to arm on page
     * load, which a modal prompt would not be.
     */
    function authenticate(urls, csrfToken, conditional) {
        return post(urls.options, {}, csrfToken).then(function (options) {
            options.challenge = base64urlToBuffer(options.challenge);
            (options.allowCredentials || []).forEach(function (credential) {
                credential.id = base64urlToBuffer(credential.id);
            });

            var request = {publicKey: options};
            if (conditional) {
                request.mediation = 'conditional';
            }
            return global.navigator.credentials.get(request);
        }).then(function (credential) {
            var response = credential.response;
            return post(urls.verify, {
                credential: {
                    id: credential.id,
                    rawId: bufferToBase64url(credential.rawId),
                    type: credential.type,
                    clientExtensionResults: credential.getClientExtensionResults(),
                    response: {
                        clientDataJSON: bufferToBase64url(response.clientDataJSON),
                        authenticatorData: bufferToBase64url(response.authenticatorData),
                        signature: bufferToBase64url(response.signature),
                        userHandle: response.userHandle
                            ? bufferToBase64url(response.userHandle)
                            : null
                    }
                }
            }, csrfToken).catch(function (error) {
                // Ask the provider to drop a credential ELSA has forgotten, so
                // it stops being offered on every future sign-in.
                if (error && error.reason === 'unknown_credential') {
                    return forget(error.credentialId).then(function () { throw error; });
                }
                throw error;
            });
        });
    }

    global.ElsaPasskeys = {
        supported: supported,
        forget: forget,
        autofillSupported: autofillSupported,
        register: register,
        authenticate: authenticate,
        describe: describe
    };
})(window);
