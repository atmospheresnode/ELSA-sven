<!-- watches: friends/views.py#profile, friends/views.py#friend_settings, friends/views.py#friend_useraccount, friends/views.py#passkey_*, friends/forms.py, friends/urls.py, static/js/account.js, templates/friends/useraccount.html -->
<!-- fingerprint:
     friends/views.py#profile            = 16f043d6b859
     friends/views.py#friend_settings    = 73e9ddd8970c
     friends/views.py#friend_useraccount = c8e3dcd86bc3
     friends/views.py#passkey_*          = f9649702c506
     friends/forms.py                    = e23a74c3ff95
     friends/urls.py                     = 324191f7496f
     static/js/account.js     = 649d9c461488
     templates/friends/useraccount.html  = 31f90ec7b0bb
-->
<!-- reviewed: 2026-08-28 -->
<!-- baseline: d8df4b96320a4636b7a95ac426bec7ff1a1fa839 -->
# Account Settings: Update Your Name, Email, Password, Passkeys, or Close Your Account

Everything about your account is on one page:

1. Click your **username** in the top-right corner of any page (it has a person
   icon next to it).
2. Choose **Account** from the dropdown menu. The left side shows your name,
   username, the year you joined ("ELSA user since ..."), and your bundle count.
3. Each setting is a row with its current value. Click **Edit** (or
   **Change**) on a row and a small window opens for just that setting; when
   you save, the page updates straight away without reloading. Problems (a
   wrong password, a taken email address) are shown in red under the field and
   nothing is changed.
   - **Name**: first and last name. **Save** turns on once you change something.
   - **Email**: type the new address and your current password, then **Send
     code**. ELSA emails a 6-digit code to the new address; type it into the
     same window and it confirms by itself once all six digits are in. The
     code lasts 5 minutes, and **Send a new code** gets another after a short
     wait. You can click **Finish later**: the page then shows "Waiting for you
     to confirm ..." with **Enter code** and **Cancel change**. Until you
     confirm, sign-in codes and password resets keep going to the old address;
     afterwards ELSA emails the old address to let you know.
   - **Password**: your current password, then the new one twice. A checklist
     under the field shows the strength rules as you type, and the eye button
     shows or hides what you typed. Changing it keeps you signed in on this
     device, signs you out everywhere else, and ELSA emails you to confirm. The
     row shows when the password was last changed.
   - **Username**: click **Edit**, type the new username and your current
     password. Letters, digits and . @ + - _ only, and it must not be taken.
     Your bundles and files move with it, and you sign in with the new name
     from then on. Passkeys keep working (a device may still show the old
     name). It cannot be changed while a validation run is in progress.
   - **Agency** (NASA, ESA, or JAXA) is part of every bundle's identifier
     (LID), so the ELSA team changes it by hand. Click **Request**, choose the
     agency you need, optionally say why, and click **Draft my email**. ELSA
     drafts the email (with AI when the assistant is available), and you can
     edit the subject and message before clicking **Send to the team**. The
     team replies to your email address, and you get a copy.

   - **Author details**: your ORCID iD and affiliation, edited together. ELSA
     checks the ORCID iD for typos (its last character is a checksum) and
     accepts the full orcid.org link too. They are used by **Add me as an
     author** in Citation Information.

Times on the Account page and in ELSA's account emails are shown in your own
time zone, taken automatically from your browser; there is nothing to set.

Security section extras:
   - **Where you're signed in** lists each browser signed in to your account,
     with when it was last active and its IP address. **Sign out** ends one;
     **Sign out everywhere else** ends all but the one you are using (and
     forgets other remembered devices). ELSA emails you when your account is
     signed in to from a device it has not seen recently.
   - **Remembered devices** are browsers where you ticked "Remember this device
     for 30 days" when entering your sign-in code; they skip the code after
     your password until the date shown. **Forget** stops that.
   - **Recent activity** lists sign-ins and changes (password, email, username,
     passkeys, devices) with time and device.

The summary card on the left shows your initials and a **My bundles** link
with your bundle count, which opens the Bundle Hub.

If ELSA emails you about a change you did not make, reset your password
straight away and contact the ELSA team.

Passkeys: the **Security** section of the Account page lists the passkeys on
your account, when each was added, and when it was last used. A passkey marked
**Synced** is stored by a password manager and works on your other devices; one
without that badge lives only on the device that created it.

- **Add a passkey**: click **Add a passkey**, give it a name you will recognise
  (for example "Work laptop"), click **Continue**, and follow your browser or
  device prompt.
- **Rename a passkey**: click **Rename** on its row.
- **Remove a passkey**: click **Remove** on its row and confirm. You will not
  be able to sign in with it again. If it was your only passkey, ELSA goes back
  to emailing you a code at each sign-in.

Because a passkey belongs to the device that made it, add one on each device
you use. Removing a passkey in ELSA does not delete it from the device itself,
so your browser may still offer it; if you pick one ELSA no longer recognises,
it will say so and you can sign in with your password instead.

Forgot your password? Use the **password reset** link on the password screen
(the second step of signing in), ELSA emails you a reset link to set a new
password without logging in. The link works once. ELSA emails you again once
the password has been reset.

Closing your account: the **Close account** section at the bottom of the
Account page. Enter your password and type your username to confirm. You are signed
out and cannot sign in again; your name, email address, and passkeys are
removed, and your username cannot be used again. Your bundles are not
deleted: they are kept by the ELSA team at the PDS Atmospheres Node and are
hidden from everyone else. Your email address can be used for a new account
later. Contact the ELSA team if you need anything from a closed account.
