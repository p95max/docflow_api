docker compose exec api alembic upgrade head

docker compose exec api alembic current
docker compose exec db psql -U docsflow -d docsflow -c "\dt"
docker compose exec db psql -U docsflow -d docsflow -c "\dT"



curl -X POST http://localhost:8000/api/v1/documents/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@$HOME/Downloads/diana_tanz.pdf;type=application/pdf" \
  -F "confidential=false"
