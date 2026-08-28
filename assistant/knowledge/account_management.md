<!-- watches: friends/views.py#profile, friends/views.py#friend_settings, friends/views.py#friend_useraccount, friends/views.py#passkey_*, friends/forms.py, friends/urls.py, templates/friends/settings.html, templates/friends/useraccount.html -->
<!-- fingerprint:
     friends/views.py#profile            = 16f043d6b859
     friends/views.py#friend_settings    = 73e9ddd8970c
     friends/views.py#friend_useraccount = c8e3dcd86bc3
     friends/views.py#passkey_*          = f9649702c506
     friends/forms.py                    = e23a74c3ff95
     friends/urls.py                     = 324191f7496f
     templates/friends/settings.html     = 649d9c461488
     templates/friends/useraccount.html  = 31f90ec7b0bb
-->
<!-- reviewed: 2026-08-28 -->
<!-- baseline: d8df4b96320a4636b7a95ac426bec7ff1a1fa839 -->
# Account Settings: Update Your Name, Email, Agency, Password, or Passkeys

To update your name (first or last name), email address, agency, or password
in ELSA, follow these steps:

1. Click your **username** in the top-right corner of any page (it has a person
   icon next to it).
2. Choose **Account** from the dropdown menu. The Account page shows your
   profile details, bundle count, and a **Security** section.
3. Click **Edit Profile**. This opens the Settings page.
4. On the Settings page you can change, each with its own form:
   - **First name** and **Last name**
   - **Email address**
   - **Agency** (NASA, ESA, or JAXA)
   - **Password**, enter your current password plus the new password twice.
     If the current password is wrong or the new passwords don't match, ELSA
     shows an error page and nothing is changed.
5. Save the form. ELSA emails you a confirmation that your profile was updated
   (if you didn't make the change, report it via the Contact page).

Passkeys: the **Security** section of the Account page lists the passkeys on
your account, when each was added, and when it was last used. A passkey marked
**Synced** is stored by a password manager and works on your other devices; one
without that badge lives only on the device that created it.

- **Add a passkey**: click **Add a passkey**, give it a name you will recognise
  (for example "Work laptop"), and follow your browser or device prompt.
- **Remove a passkey**: click **Remove** on its row and confirm. You will not
  be able to sign in with it again. If it was your only passkey, ELSA goes back
  to emailing you a code at each sign-in.

Because a passkey belongs to the device that made it, add one on each device
you use. Removing a passkey in ELSA does not delete it from the device itself,
so your browser may still offer it; if you pick one ELSA no longer recognises,
it will say so and you can sign in with your password instead.

Forgot your password? Use the **password reset** link on the password screen
(the second step of signing in), ELSA emails you a reset link to set a new
password without logging in.

The username itself (login name) cannot be changed from the Settings page -
contact the ELSA team via the Contact page if you need that changed.
