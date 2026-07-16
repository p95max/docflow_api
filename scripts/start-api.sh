#!/bin/sh

set -eu

echo "Validating application configuration..."
# Configuration errors are deterministic and must fail immediately. Retrying
# them as if PostgreSQL were still starting only hides the actionable error.
python -c "from app.core.config import settings; print('Application configuration is valid.')"

echo "Checking for pending database migrations..."

attempt=1
max_attempts=10

# `upgrade head` is safe when the database is already current: Alembic exits
# successfully without applying anything. New revisions copied or mounted into
# the container are applied on every full container start before the API boots.
until alembic upgrade head; do
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "Database migrations failed after $max_attempts attempts."
    exit 1
  fi

  echo "Migration attempt $attempt failed. Retrying in 3 seconds..."
  attempt=$((attempt + 1))
  sleep 3
done

echo "Database schema is at the latest Alembic revision."
echo "Encrypting Google Drive refresh tokens with the active key..."
python -m scripts.rotate_google_drive_tokens
echo "Initializing local test user..."
python -m scripts.init_test_user

echo "Starting API server..."

if [ "${APP_ENV:-local}" = "local" ]; then
  watch_migrations() {
    last_signature="$(find alembic/versions -type f -name '*.py' -printf '%T@:%s:%p\n' | sort | sha256sum | awk '{print $1}')"

    while sleep 2; do
      current_signature="$(find alembic/versions -type f -name '*.py' -printf '%T@:%s:%p\n' | sort | sha256sum | awk '{print $1}')"
      [ "$current_signature" = "$last_signature" ] && continue

      echo "Detected a migration change. Applying pending revisions..."
      until alembic upgrade head; do
        echo "Migration watcher retrying in 3 seconds..."
        sleep 3
      done
      echo "Database schema is at the latest Alembic revision."
      last_signature="$current_signature"
    done
  }

  # Codespaces mounts the workspace into an already running container. A source
  # edit only triggers Uvicorn reload, not this entrypoint, so watch revisions
  # in local development and apply them without a manual container restart.
  watch_migrations &
  exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${WEB_CONCURRENCY:-2}"
