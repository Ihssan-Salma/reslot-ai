# Google Cloud configuration

ReSlot AI uses a confidential web-server OAuth client. Each business owner/admin connects
an account through `/settings/google`; this is separate from signing into ReSlot AI.
No API key, service account, browser SDK, or domain-wide delegation is used.

## Configure a development Google Cloud project

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create/select a development
   project, for example `ReSlot AI Development`.
2. Under **APIs & Services > Library**, enable **Gmail API**, **Google Calendar API**, and **Google Sheets API**.
3. Open **Google Auth Platform**. If prompted, click **Get started**. Under **Branding**, enter
   the app name `ReSlot AI`, your support email, and developer contact email; save.
4. Under **Audience**, choose **External**, retain publishing status **Testing**, and add your
   real Google test account under **Test users > Add users**. Internal is suitable only for a
   project tied to a Workspace organization whose users should be the sole audience.
5. Under **Data Access > Add or remove scopes**, add the seven scopes below and save. If the
   picker displays the email permission as `.../auth/userinfo.email`, that is the canonical
   alias accepted by this implementation. These steps follow Google's
   [consent configuration guide](https://developers.google.com/workspace/guides/configure-oauth-consent).

```text
openid
email
https://www.googleapis.com/auth/gmail.readonly
https://www.googleapis.com/auth/gmail.send
https://www.googleapis.com/auth/calendar.events
https://www.googleapis.com/auth/calendar.calendarlist.readonly
https://www.googleapis.com/auth/spreadsheets
```

6. Under **Clients > Create client**, choose **Web application** and name it `ReSlot AI Local`.
   Add this exact **Authorized redirect URI**:

```text
http://localhost:8000/api/v1/oauth/google/callback
```

7. Create the client and copy its **Client ID** and **Client secret** into the backend settings
   below. Do not select Desktop application. Authorized JavaScript origins can remain empty:
   the browser does not call Google APIs or exchange codes. Google's
   [client configuration guide](https://support.google.com/cloud/answer/15549257) describes
   these fields and exact redirect matching.

Keep localhost spelling, port, path, and trailing-slash behavior identical to GOOGLE_REDIRECT_URI.
If instead routing the callback through Nginx at port 5173, register
`http://localhost:5173/api/v1/oauth/google/callback` and change the environment value to match.
The default direct API callback works because local cookies are scoped to host, not port.

## Configure ReSlot AI

Preserve your existing root `.env` and database settings. Set these four variables:

```dotenv
GOOGLE_CLIENT_ID=<your-web-client-id>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<your-web-client-secret>
GOOGLE_REDIRECT_URI=http://localhost:8000/api/v1/oauth/google/callback
GOOGLE_TOKEN_ENCRYPTION_KEY=<one-generated-Fernet-key>
```

From the repository root, generate the encryption key once after installing dependencies:

```powershell
.\.venv\Scripts\python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy that generated value into `.env`; keep it stable across restarts and back it up separately
from the database. It is a URL-safe base64 encoded 32-byte key. Existing refresh tokens cannot
be decrypted with a replacement key. Key rotation requires re-encryption under controlled
administrative tooling or revoking grants and reconnecting accounts; no automatic rotation
job is included. Neither credentials nor this key belong in any VITE_* variable or frontend file.
Do not upload downloaded OAuth credential JSON into the repository.

Set `CORS_ORIGINS=["http://localhost:5173"]` for the default UI. The first trusted origin is also
the fixed post-OAuth redirect destination; no client can supply a different return URL.

```powershell
docker compose up --build -d
Start-Process http://localhost:5173/settings/google
```

For local Python/Vite development, restart the API after changing `.env`; the existing startup
commands in README apply. A missing or malformed key disables Connect and produces a safe
configuration error. Other application features continue to work without Google credentials.

For production, register the exact HTTPS callback for your deployed domain, use HTTPS trusted
origins, APP_ENV=production and COOKIE_SECURE=true, and inject secrets through your secret
manager. Use a separate development client/project for localhost.

## Google test-account limitations

External apps in Testing issue refresh tokens that normally expire after seven days for
these scopes; ReSlot AI then requests reconnection. See Google's
[token lifecycle documentation](https://developers.google.com/identity/protocols/oauth2).
Workspace administrators can block the app or scopes even for configured test users; use an
allowed test account or have the organization's administrator approve the app.

Gmail readonly is a restricted scope. Production distribution requires the applicable Google
verification and potentially a security assessment for server-side restricted data access.
The send and event-write permissions are requested as specified for future tasks, but Task 03
contains no mail sending, monitoring, email analysis, or event mutation. Google's
[Gmail scope classifications](https://developers.google.com/workspace/gmail/api/auth/scopes)
explain the relevant categories. Do not treat a successful local test as production verification.

See [Sheets scope rationale and template](google-sheets.md) and the
[real-account smoke test](integration-smoke-test.md). Existing connections must reconnect to grant Sheets access.
