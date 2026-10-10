#!/usr/bin/env bash
# db/db.sh: create the database, create/refresh the least-privilege Toolbox role, reset the data.
# Usage: db/db.sh setup   (once: database + role)   |   db/db.sh reset   (every eval run)
# Reads DB_NAME, DB_HOST, DB_PORT, DB_USER, DB_PASSWORD from the environment or .env.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"

if [ -f .env ]; then set -a; . ./.env; set +a; fi
DB_NAME="${DB_NAME:-support}"; DB_HOST="${DB_HOST:-127.0.0.1}"; DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-toolbox}"
ADMIN=(psql -h "$DB_HOST" -p "$DB_PORT" -v ON_ERROR_STOP=1 -q)   # local superuser = the macOS user

case "${1:-}" in
  setup)
    : "${DB_PASSWORD:?DB_PASSWORD must be set in .env}"
    "${ADMIN[@]}" -d postgres -tc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 \
      || "${ADMIN[@]}" -d postgres -c "CREATE DATABASE $DB_NAME"
    "${ADMIN[@]}" -d postgres -v pw="$DB_PASSWORD" <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DB_USER') THEN CREATE ROLE $DB_USER LOGIN; END IF;
END \$\$;
ALTER ROLE $DB_USER PASSWORD :'pw';
SQL
    "${ADMIN[@]}" -d "$DB_NAME" -f db/seed.sql
    # Least privilege: read orders/users, insert into actions_log. Nothing can UPDATE or DELETE customer_orders.
    "${ADMIN[@]}" -d "$DB_NAME" <<SQL
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM $DB_USER;
GRANT CONNECT ON DATABASE $DB_NAME TO $DB_USER;
GRANT USAGE ON SCHEMA public TO $DB_USER;
GRANT SELECT ON users, customer_orders TO $DB_USER;
GRANT SELECT, INSERT ON actions_log TO $DB_USER;
GRANT USAGE ON SEQUENCE actions_log_id_seq TO $DB_USER;
SQL
    echo "setup ok: database $DB_NAME, role $DB_USER"
    ;;
  reset)
    "${ADMIN[@]}" -d "$DB_NAME" -f db/seed.sql
    echo "reset ok"
    ;;
  *) echo "usage: db/db.sh setup|reset" >&2; exit 2 ;;
esac
