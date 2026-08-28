<!-- watches: friends/views.py#register, friends/views.py#login_*, friends/views.py#friend_login, friends/views.py#otp_*, build/forms.py#BundleForm, templates/friends/register.html, templates/friends/registration -->
<!-- fingerprint:
     friends/views.py#register       = a2f2c17c829e
     friends/views.py#login_*        = 858be622daa7
     friends/views.py#friend_login   = 153e557000da
     friends/views.py#otp_*          = 772a06512da4
     build/forms.py#BundleForm       = 89db5879e013
     templates/friends/register.html = 3cbdb9e6cf98
     templates/friends/registration  = 627fda119d56
-->
<!-- reviewed: 2026-08-28 -->
<!-- baseline: d8df4b96320a4636b7a95ac426bec7ff1a1fa839 -->
# Getting Started: Registration, Login, and Your First Bundle

Registering: create an account from the ELSA registration page with your name,
email, agency (NASA, ESA, or JAXA), and a password. Each email address and
username can only be used once, and the password has to pass ELSA's strength
rules. ELSA emails you a one-time verification code (OTP), enter it on the
verification page to activate the account. Until the code is verified the
account stays inactive.

Logging in: the landing page asks for your **username only**, then a
**Continue** button. There is no password box on that first screen. What
happens next depends on the account:

- If you have set up a passkey on that device, ELSA goes straight to the
  passkey screen and your browser asks for your fingerprint, face, or device
  PIN. That signs you in on its own, no password and no emailed code.
- Otherwise ELSA shows a password screen. After the password it emails you a
  one-time code to finish signing in.

If your device already holds a saved passkey, your browser will usually offer
it as soon as you click the username box, so you never reach the second screen
at all.

The **Forgot password?** link is on the password screen, not the landing page.

Passkeys: a passkey signs you in with your fingerprint, face, or device PIN
instead of a password and an emailed code. After signing in on a device that
has never used one, ELSA offers to set one up; you can also add or remove
passkeys any time from the Account page. Passkeys stay on the device that
created them unless your password manager syncs them, so each device generally
needs its own. If you lose a device, sign in with your password and emailed
code as usual.

Creating your first bundle:
1. Click your username (top right) and open the **Bundle Hub**, this page
   lists all your bundles.
2. Click the **create bundle** button.
3. Give the bundle a **name** (this determines the auto-generated Bundle ID /
   LID, so choose carefully, e.g. "mars gcm runs" becomes
   urn:nasa:pds:mars_gcm_runs) and choose the **bundle type**:
   - **External**, for the Atmospheres Model Archive (AMA); data hosted
     externally, ELSA builds the metadata bundle. Simplest option.
   - **Archive**, a full PDS4 archive bundle with data stored in the archive.
4. You land on the bundle page, where the Bundle Components card shows what to
   complete next.

There is also a **Tutorial** section in ELSA with step-by-step guides on
building a bundle, bundles and collections, and the context, data, and document
collections.
