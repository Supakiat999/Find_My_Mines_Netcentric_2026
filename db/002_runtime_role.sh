#!/bin/sh
set -eu
: "${RUNTIME_PASSWORD:?RUNTIME_PASSWORD is required}"
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set ON_ERROR_STOP=1 --set runtime_password="$RUNTIME_PASSWORD" <<'SQL'
CREATE ROLE runtime LOGIN PASSWORD :'runtime_password';
GRANT CONNECT ON DATABASE find_my_mines TO runtime;
GRANT USAGE ON SCHEMA public TO runtime;
GRANT SELECT, INSERT, UPDATE ON players, player_ratings TO runtime;
GRANT SELECT, INSERT ON matches, match_participants TO runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO runtime;
SQL
