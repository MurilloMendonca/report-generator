#!/bin/sh
# A local Docker stack with its own env file and persistent volumes.
set -eu
cd "$(dirname "$0")/.."

local_action="${1:-up}"
if [ "$#" -gt 0 ]; then shift; fi

local_init() {
    if [ ! -e .env.local ]; then
        umask 077
        local_db_password="$(openssl rand -hex 24)"
        local_encryption_key="$(openssl rand -base64 32)"
        # noclobber also protects an existing file if another init races us.
        (set -C; sed \
            -e "s|GENERATE_POSTGRES_PASSWORD|$local_db_password|" \
            -e "s|GENERATE_ENCRYPTION_KEY|$local_encryption_key|" \
            .env.local.example > .env.local)
        printf '%s\n' 'Created .env.local with new database and encryption secrets.'
    fi
}

local_compose() {
    docker compose --project-name repgen-local --env-file .env.local --file compose.local.yml "$@"
}

case "$local_action" in
    init)
        local_init
        printf '%s\n' 'Set your endpoint URL and model IDs in .env.local, then run: sh scripts/local.sh up'
        ;;
    up)
        local_init
        local_compose up --build --detach --wait "$@"
        local_compose exec -T app python -m app.cli create-user --email local@example.com
        printf '%s\n' 'Sign in as local@example.com at http://localhost:8000 (or LOCAL_PORT from .env.local).'
        printf '%s\n' 'Use the password printed on first startup. Reset it with: sh scripts/local.sh exec app python -m app.cli set-password --email local@example.com'
        ;;
    down|stop|start|restart|logs|ps|exec|config)
        local_compose "$local_action" "$@"
        ;;
    *)
        printf '%s\n' 'Usage: sh scripts/local.sh [init|up|down|stop|start|restart|logs|ps|exec|config] [compose options]' >&2
        exit 2
        ;;
esac
