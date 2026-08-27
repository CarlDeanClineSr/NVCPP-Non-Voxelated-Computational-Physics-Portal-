# NVCPP Google Drive Vault Setup

The durable vault has already been created in Carl's Google Drive:

```text
LUFT CLINE/NVCPP_DATA_VAULT
Vault root folder ID: 1vnJClm_J0EdgFiIUmF4v3O6GYhzDwAOS
Hourly destination folder ID: 1hpgY8SYPpp6_rFv7PV61cuqUSUwTZC20
```

The vault contains top-level folders for raw data, canonical data, quarantine, hourly runs, events, pairings, charts, capsules, manifests, and status.

## Why a one-time credential step is still required

The ChatGPT Google Drive connection and a GitHub Actions runner are separate authenticated systems. A GitHub runner cannot reuse the ChatGPT connection. The workflow therefore needs one encrypted GitHub Actions credential that is authorized to create files in the vault.

## Repository secrets

Create these two repository secrets:

```text
NVCPP_GOOGLE_AUTH_B64
NVCPP_DRIVE_PARENT_FOLDER_ID
```

Set the folder secret to:

```text
1hpgY8SYPpp6_rFv7PV61cuqUSUwTZC20
```

`NVCPP_GOOGLE_AUTH_B64` is a base64-encoded Google credential JSON. The uploader supports two credential types:

### Personal My Drive — authorized user credential

Use an OAuth authorized-user JSON containing:

```json
{
  "type": "authorized_user",
  "client_id": "...",
  "client_secret": "...",
  "refresh_token": "..."
}
```

This is the appropriate mode for the existing user-owned `LUFT CLINE` folder.

### Shared Drive — service account credential

A dedicated service account can be used when the destination is moved into a Google Shared Drive and that service account has contributor access.

## Encoding

On Python:

```python
import base64
from pathlib import Path

encoded = base64.b64encode(Path("google-credential.json").read_bytes()).decode()
print(encoded)
```

Copy the printed one-line value into the `NVCPP_GOOGLE_AUTH_B64` repository secret. Do not commit the JSON or encoded value to the repository.

## Create-only behavior

The uploader:

- creates one unique Drive folder for each observatory run;
- refuses a same-name run-folder collision;
- refuses same-name file collisions;
- attaches the run ID and SHA-256 as Drive application properties;
- uploads the receipt last;
- never updates, overwrites, deletes, or silently replaces existing vault evidence.

## Until the secrets are installed

The workflow still runs, charts, analyzes, and uploads a temporary GitHub artifact. It creates a local `drive_upload_receipt.json` with status `DRY_RUN`, making the missing durable publication visible rather than pretending Drive succeeded.
