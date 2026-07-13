# Google Drive JSON Backup

MVP 1.5 exports the authenticated user's DocsFlow data to a compressed JSON file and uploads it to Google Drive.

## Architecture

The API creates a `BackupJob` synchronously and delegates the export/upload work to Celery.

```text
POST /api/v1/backups/run
  -> create BackupJob(status=pending)
  -> enqueue Celery task
  -> status=running
  -> export allow-listed database records
  -> JSON -> gzip -> SHA-256
  -> upload to Google Drive
  -> status=completed or failed
```

For the MVP, DocsFlow uses one technical Google account configured through environment variables. Application users do not connect their own Google Drive accounts. Each backup payload remains isolated by `owner_id`.

## Local startup user

After Alembic migrations complete, `scripts/start-api.sh` runs `scripts/init_test_user.py`.

When `APP_ENV=local` and `INIT_TEST_USER=true`, the script idempotently creates or updates this development account:

```text
email: m@m.com
password: 12345678
```

The credentials can be overridden through `TEST_USER_EMAIL` and `TEST_USER_PASSWORD`. The initializer is skipped unless both local mode and the explicit init flag are enabled.

## Google configuration

Enable the Google Drive API in the Google Cloud project and create OAuth credentials with offline access.

The refresh token must include this scope:

```text
https://www.googleapis.com/auth/drive.file
```

Configure the API and Celery worker with the same environment variables:

```env
GOOGLE_DRIVE_CLIENT_ID=
GOOGLE_DRIVE_CLIENT_SECRET=
GOOGLE_DRIVE_REFRESH_TOKEN=
GOOGLE_DRIVE_FOLDER_NAME=docsflow_backups
GOOGLE_DRIVE_TIMEOUT_SECONDS=60

BACKUP_SOFT_TIME_LIMIT_SECONDS=120
BACKUP_HARD_TIME_LIMIT_SECONDS=180
```

The folder is looked up or created in the root of the configured Google Drive account.

## API

Create a backup:

```bash
curl -X POST http://localhost:8000/api/v1/backups/run \
  -H "Authorization: Bearer $TOKEN"
```

List backup history:

```bash
curl http://localhost:8000/api/v1/backups \
  -H "Authorization: Bearer $TOKEN"
```

Get one backup job:

```bash
curl http://localhost:8000/api/v1/backups/1 \
  -H "Authorization: Bearer $TOKEN"
```

A successful job contains the Drive file metadata, compressed size, SHA-256 checksum and record counts.

## Backup format

The uploaded file name has this form:

```text
docsflow-backup-user-<owner_id>-<UTC timestamp>.json.gz
```

The JSON contains:

- user profile fields required for restore;
- document records and file metadata;
- extracted text and structured extraction results;
- processing jobs;
- OpenAI usage records;
- audit logs;
- backup job metadata.

The original uploaded PDF/JPG/PNG files are not included.

## Security boundary

Serialization uses explicit field allow-lists. The backup excludes:

- password hashes;
- Google OAuth client secret and refresh token;
- API keys and OpenAI credentials;
- Celery task identifiers.

Google credentials remain environment-only and are never written to the backup payload or database.

## Validation

Run the backup and startup tests:

```bash
docker compose run --rm api pytest \
  tests/test_backups.py \
  tests/test_backup_hardening.py \
  tests/test_backup_frontend.py \
  tests/test_init_test_user.py
```

Run all tests:

```bash
docker compose run --rm api pytest
```
