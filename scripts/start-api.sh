#!/bin/sh

set -e

echo "Running database migrations..."

attempt=1
max_attempts=10

until alembic upgrade head; do
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "Database migrations failed after $max_attempts attempts."
    exit 1
  fi

  echo "Migration attempt $attempt failed. Retrying in 3 seconds..."
  attempt=$((attempt + 1))
  sleep 3
done

echo "Database migrations completed."
echo "Starting API server..."

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload