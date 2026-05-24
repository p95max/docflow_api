docker compose exec api alembic upgrade head

docker compose exec api alembic current
docker compose exec db psql -U docsflow -d docsflow -c "\dt"
docker compose exec db psql -U docsflow -d docsflow -c "\dT"