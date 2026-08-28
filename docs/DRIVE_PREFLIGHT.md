# NVCPP Drive Vault Preflight

Use the **NVCPP Drive Vault Preflight** workflow before rerunning the complete hourly observatory after any Google credential change.

The preflight performs no Drive writes. It verifies:

- the repository secrets are present;
- `NVCPP_GOOGLE_AUTH_B64` is strict Base64 JSON;
- the credential type and required fields are valid;
- Google can refresh the credential;
- `NVCPP_DRIVE_PARENT_FOLDER_ID` is visible to that credential;
- the destination is an untrashed Drive folder;
- the credential can add children to the folder.

It uploads a sanitized `drive_preflight_result.json` artifact. Tokens, client secrets, refresh tokens, and private keys are never included.

## Run it

```text
Repository
→ Actions
→ NVCPP Drive Vault Preflight
→ Run workflow
→ main
→ Run workflow
```

A successful result looks like:

```json
{
  "status": "SUCCESS",
  "credential_type": "authorized_user",
  "writes_performed": false,
  "folder": {
    "id": "1hpgY8SYPpp6_rFv7PV61cuqUSUwTZC20",
    "can_add_children": true
  }
}
```

## Meaning of `invalid_client`

`invalid_client` is not a telemetry, physics, test, branch, or folder problem. It means the OAuth `client_id` and `client_secret` in the bundled `authorized_user` JSON are not the matching client that issued the `refresh_token`.

Replace the complete JSON bundle together. Do not replace only one field.

```json
{
  "type": "authorized_user",
  "client_id": "ONE_DEDICATED_NVCPP_CLIENT_ID",
  "client_secret": "THE_SECRET_FOR_THAT_SAME_CLIENT",
  "refresh_token": "A_NEW_TOKEN_ISSUED_USING_THAT_SAME_CLIENT"
}
```

When OAuth Playground is used, enable **Use your own OAuth credentials** before authorization. A refresh token issued under OAuth Playground's default client cannot be repaired by combining it with a different Cloud project's client secret.

Any authorization code, access token, refresh token, or client secret pasted into chat or another public location must be revoked and replaced before testing again.

## Correct order after replacement

1. Revoke the exposed or mismatched Google grant.
2. Create one dedicated NVCPP OAuth client.
3. Generate a new offline refresh token using that exact client ID and secret.
4. Build one `authorized_user` JSON with the three matched fields.
5. Base64-encode the complete JSON file.
6. Replace `NVCPP_GOOGLE_AUTH_B64` in GitHub repository secrets.
7. Keep `NVCPP_DRIVE_PARENT_FOLDER_ID` set to the intended destination.
8. Run **NVCPP Drive Vault Preflight**.
9. Run **NVCPP Hourly Observatory** only after preflight is green.
