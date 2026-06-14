docker compose exec api alembic upgrade head

docker compose exec api alembic current
docker compose exec db psql -U docsflow -d docsflow -c "\dt"
docker compose exec db psql -U docsflow -d docsflow -c "\dT"



curl -X POST http://localhost:8000/api/v1/documents/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@$HOME/Downloads/diana_tanz.pdf;type=application/pdf" \
  -F "confidential=false"

# register
curl -sS -X POST http://localhost:8000/api/v1/users/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test1@example.com",
    "password": "testpass123"
  }' | jq

# get token
TOKEN=$(curl -sS -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=test1@example.com" \
  -d "password=testpass123" |
  jq -r '.access_token // empty')

# check token
echo "$TOKEN"

# auth
curl -sS http://localhost:8000/api/v1/users/me \
  -H "Authorization: Bearer $TOKEN" | jq


# tests
docker compose run --rm api pytest