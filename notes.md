docker compose exec api alembic upgrade head

docker compose exec api alembic current
docker compose exec db psql -U docsflow -d docsflow -c "\dt"
docker compose exec db psql -U docsflow -d docsflow -c "\dT"



curl -X POST http://localhost:8000/api/v1/documents/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@$HOME/Downloads/diana_tanz.pdf;type=application/pdf" \
  -F "confidential=false"

export TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIiwiZXhwIjoxNzc5NzEyOTE5fQ.584oWiKyGdJ0Nroh5r03-j2o0QmyDo7D8hyX1ZCES9s"