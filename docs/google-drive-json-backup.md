# Google Drive JSON Backup

MVP 1.5 exports the authenticated user's DocsFlow records and document metadata to a compressed JSON file and uploads it to that user's connected Google Drive account.

## Flow

1. The user opens **Backups** and clicks **Connect Google Drive**.
2. DocsFlow redirects the browser to Google's OAuth consent page.
3. Google returns an authorization code to `/backups/google/callback`.
4. DocsFlow validates a signed state value and browser nonce, then exchanges the code server-side.
5. The returned offline credential is stored for that DocsFlow user.
6. A backup job uses the stored credential to obtain a short-lived access token and upload the archive.

The user never copies an OAuth token into the application configuration.

## Google Cloud setup

Enable the Google Drive API and create an OAuth client of type **Web application**. Add the exact DocsFlow callback URI to the client's authorized redirect URIs.

For local development, the callback is normally:

`http://localhost:8000/backups/google/callback`

For GitHub Codespaces, use the public forwarded-port origin followed by:

`/backups/google/callback`

The callback scheme, hostname, port, path, and trailing slash must match exactly.

The application still needs its Google OAuth client identifier and client credential configured server-side. An explicit callback setting is recommended behind a proxy or in Codespaces. The legacy manually supplied Drive token setting is ignored by the new browser flow.

DocsFlow requests only the `drive.file` permission. This allows it to create and manage files created by DocsFlow without general access to all files in the user's Drive.

## Backup processing

```text
Create backup
  -> require connected Google Drive account
  -> create BackupJob(status=pending)
  -> enqueue Celery task
  -> export allow-listed database records
  -> JSON -> gzip -> SHA-256
  -> upload to /docsflow_backups
  -> status=completed or failed
```

Original PDF, JPG, and PNG files are not included.

## Security

- OAuth callback state is signed and expires quickly.
- The state is also bound to an HttpOnly browser nonce.
- Each Drive connection is isolated by DocsFlow user ID.
- Password hashes, OAuth credentials, API keys, and Celery task identifiers are excluded from backup archives.
- Disconnecting Drive attempts to revoke the Google grant and removes the local connection.

The stored offline credential is sensitive. Production databases should use encryption at rest and tightly restricted access.

## Validation

Run the focused suite:

```bash
docker compose run --rm api pytest \
  tests/test_backups.py \
  tests/test_backup_hardening.py \
  tests/test_backup_frontend.py \
  tests/test_google_drive_oauth.py \
  tests/test_init_test_user.py
```

Run the full suite:

```bash
docker compose run --rm api pytest
```
