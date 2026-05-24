curl -X POST http://localhost:8000/api/v1/users/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"max@example.com","password":"strong-password"}'

docker compose exec db psql -U docsflow -d docsflow -c "SELECT id, email, is_active, created_at FROM users;"

curl -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'username=max@example.com&password=strong-password'