# NVCPP Google Drive Vault Setup

The durable vault has already been created in Carl's Google Drive:

```text
LUFT CLINE/NVCPP_DATA_VAULT
Vault root folder ID: 1vnJClm_J0EdgFiIUmF4v3O6GYhzDwAOS
Hourly destination folder ID: 1hpgY8SYPpp6_rFv7PV61cuqUSUwTZC20
```

The vault contains top-level folders for raw data, canonical data, quarantine,
hourly runs, events, pairings, charts, capsules, manifests, and status.

## Security rule

Never paste an OAuth authorization code, access token, refresh token, client
secret, service-account private key, credential JSON, or Base64 credential into
chat, an issue, a pull request, a workflow file, a commit, or an Actions log.
Base64 is an encoding, not encryption.

If any credential is exposed:

1. Revoke the OAuth grant or token immediately.
2. Delete every GitHub secret created from that credential.
3. Rotate the OAuth client secret when it belongs to your Cloud project.
4. Generate a completely new refresh token.
5. Add only the replacement credential to GitHub Actions.

The repository uses one bundled credential secret. Obsolete split secrets such
as `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN` are not
part of the NVCPP production contract and should be deleted.

## Why a one-time credential step is required

The ChatGPT Google Drive connection and a GitHub Actions runner are separate
authenticated systems. A GitHub runner cannot reuse the ChatGPT connection. The
workflow therefore needs one encrypted GitHub Actions credential authorized to
create files in the vault.

## Required repository secrets

Create exactly these two repository secrets:

```text
NVCPP_GOOGLE_AUTH_B64
NVCPP_DRIVE_PARENT_FOLDER_ID
```

For the existing user-owned My Drive destination, set:

```text
NVCPP_DRIVE_PARENT_FOLDER_ID=1hpgY8SYPpp6_rFv7PV61cuqUSUwTZC20
```

`NVCPP_GOOGLE_AUTH_B64` is a one-line Base64 encoding of a complete Google
credential JSON. The publisher supports `authorized_user` and
`service_account` credentials.

## Personal My Drive: authorized-user credential

This is the correct mode for the existing `LUFT CLINE` folder.

### Create a dedicated OAuth client

1. Create or select a Google Cloud project dedicated to NVCPP.
2. Enable the Google Drive API.
3. Configure the OAuth consent screen for Carl's account.
4. Move the app to **In production** before relying on unattended hourly runs.
   External apps left in **Testing** commonly receive refresh tokens that expire
   after seven days.
5. Create a Web application OAuth client.
6. Add this authorized redirect URI temporarily:

```text
https://developers.google.com/oauthplayground
```

7. In OAuth Playground, open the configuration gear, select **Use your own OAuth
   credentials**, and enter the dedicated NVCPP client ID and client secret.
8. Authorize only the Drive scope required by the current publisher:

```text
https://www.googleapis.com/auth/drive
```

9. Exchange the authorization code for tokens.
10. Do not copy the response into chat or a repository file.

A durable token response should not report a seven-day
`refresh_token_expires_in` value. If it does, correct the OAuth publishing state
and generate a replacement token.

### Build the credential JSON locally

Create a local file named `google-credential.json` outside the repository:

```json
{
  "type": "authorized_user",
  "client_id": "YOUR_DEDICATED_NVCPP_CLIENT_ID",
  "client_secret": "YOUR_ROTATED_NVCPP_CLIENT_SECRET",
  "refresh_token": "YOUR_NEW_UNEXPOSED_REFRESH_TOKEN"
}
```

Encode it locally with Python:

```python
import base64
from pathlib import Path

encoded = base64.b64encode(
    Path("google-credential.json").read_bytes()
).decode("ascii")
print(encoded)
```

Copy the one-line output directly into the `NVCPP_GOOGLE_AUTH_B64` repository
secret. Delete the local plaintext file after the secret is installed and
verified, or store it only in an encrypted password manager.

## Shared Drive: service-account credential

A service account is appropriate only after the destination is moved to a
Google Shared Drive and the service-account email is added to that Shared Drive
with contributor access. Encode the downloaded service-account JSON exactly as
above and use the Shared Drive `hourly` folder ID for
`NVCPP_DRIVE_PARENT_FOLDER_ID`.

Do not assume that sharing a normal personal My Drive folder with a service
account provides the same ownership and storage behavior as a Shared Drive.

## Add the secrets in GitHub

Open the repository and navigate to:

```text
Settings
→ Secrets and variables
→ Actions
→ New repository secret
```

Add:

```text
Name: NVCPP_GOOGLE_AUTH_B64
Value: the one-line Base64 credential JSON
```

Then add:

```text
Name: NVCPP_DRIVE_PARENT_FOLDER_ID
Value: 1hpgY8SYPpp6_rFv7PV61cuqUSUwTZC20
```

Delete obsolete repository secrets if they exist:

```text
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_REFRESH_TOKEN
```

## Verification

Run **NVCPP Hourly Observatory** manually. A correct run should show:

```text
Publish immutable package to Google Drive  success
Record Drive-not-configured state          skipped
```

The run package should contain `drive_upload_receipt.json` with status
`SUCCESS`, and the Drive `hourly` folder should contain one new uniquely named
run folder.

If publication fails, revoke and rotate rather than pasting the token response
into a troubleshooting message.

## Create-only behavior

The uploader:

- creates one unique Drive folder for each observatory run;
- refuses a same-name run-folder collision;
- refuses same-name file collisions;
- attaches the run ID and SHA-256 as Drive application properties;
- uploads the receipt last;
- never updates, overwrites, deletes, or silently replaces existing evidence.

## Until the secrets are installed

The workflow still runs, charts, analyzes, and uploads a temporary GitHub
artifact. It creates a local `drive_upload_receipt.json` with status `DRY_RUN`,
making the missing durable publication visible rather than pretending Drive
succeeded.
