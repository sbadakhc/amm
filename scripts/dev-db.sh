#!/usr/bin/env bash
# Throwaway Postgres (pgvector/pgvector:pg16 -- pgvector needed for
# listing_embeddings, §6) for local testing -- see AGENTS.md §5.
#
# Usage:
#   scripts/dev-db.sh up      # start container, apply schema.sql, print DATABASE_URL
#   scripts/dev-db.sh seed    # run generate_synthetic_data.py against it
#   scripts/dev-db.sh psql    # open a psql shell against it
#   scripts/dev-db.sh down    # stop and remove the container
#
# Nothing here touches real infrastructure -- container name/port are fixed and
# dedicated to this script so re-running `up` after a crash is safe.

set -euo pipefail

CONTAINER_NAME="amm-postgres"
PORT="55432"
DB_USER="amm"
DB_PASSWORD="amm"
DB_NAME="moderator"
DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@127.0.0.1:${PORT}/${DB_NAME}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cmd="${1:-}"

case "$cmd" in
  up)
    if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
      echo "Container $CONTAINER_NAME already exists -- run 'scripts/dev-db.sh down' first."
      exit 1
    fi
    docker run -d --name "$CONTAINER_NAME" --network bridge \
      -e POSTGRES_USER="$DB_USER" -e POSTGRES_PASSWORD="$DB_PASSWORD" -e POSTGRES_DB="$DB_NAME" \
      -p "${PORT}:5432" pgvector/pgvector:pg16 >/dev/null

    echo "Waiting for Postgres to accept connections..."
    for _ in $(seq 1 15); do
      if docker exec "$CONTAINER_NAME" pg_isready -U "$DB_USER" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done

    PGPASSWORD="$DB_PASSWORD" psql -h 127.0.0.1 -p "$PORT" -U "$DB_USER" -d "$DB_NAME" \
      -f "$SCRIPT_DIR/schema.sql"

    echo ""
    echo "Postgres is up. Set this in your shell (or .env):"
    echo "  export DATABASE_URL=\"$DATABASE_URL\""
    ;;
  seed)
    DATABASE_URL="$DATABASE_URL" python3 "$SCRIPT_DIR/generate_synthetic_data.py"
    ;;
  psql)
    PGPASSWORD="$DB_PASSWORD" psql -h 127.0.0.1 -p "$PORT" -U "$DB_USER" -d "$DB_NAME"
    ;;
  down)
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    echo "Removed $CONTAINER_NAME."
    ;;
  *)
    echo "Usage: $0 {up|seed|psql|down}"
    exit 1
    ;;
esac
