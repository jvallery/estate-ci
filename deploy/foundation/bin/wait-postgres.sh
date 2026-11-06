#!/usr/bin/env bash
# estate-ci: wait for the pg-profile Postgres sidecar and print its env (eval-able).
#   eval "$(/opt/estate-ci/wait-postgres.sh)"
set -euo pipefail
for _ in $(seq 1 120); do
  if (exec 3<>/dev/tcp/127.0.0.1/5432) 2>/dev/null; then
    printf 'export PGHOST=127.0.0.1 PGPORT=5432 PGUSER=postgres PGDATABASE=postgres DATABASE_URL=postgresql://postgres@127.0.0.1:5432/postgres\n'
    exit 0
  fi
  sleep 1
done
echo "no Postgres sidecar on 127.0.0.1:5432 (runs-on must be a <repo>-pg set)" >&2
exit 1
