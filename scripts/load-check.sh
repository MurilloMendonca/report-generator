#!/bin/sh
# Run from the Mac host: sh scripts/load-check.sh [5..10]
# A unique Compose project owns every test container, volume and network.
set -eu

case "${1:-10}" in
    5|6|7|8|9|10) REPGEN_LOAD_USERS="${1:-10}" ;;
    *) printf '%s\n' 'Usage: sh scripts/load-check.sh [5..10]' >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
    printf '%s\n' 'Usage: sh scripts/load-check.sh [5..10]' >&2
    exit 2
fi
export REPGEN_LOAD_USERS
cd "$(dirname "$0")/.."
load_project="repgen-load-$(date +%s)-$$"

load_compose() {
    docker compose --project-name "$load_project" --file compose.load.yml "$@"
}

load_cleanup() {
    load_status=$?
    trap - EXIT
    if ! load_compose down --volumes --remove-orphans; then
        printf 'Could not clean up test project %s. Run: docker compose -p %s -f compose.load.yml down --volumes\n' "$load_project" "$load_project" >&2
        if [ "$load_status" -eq 0 ]; then load_status=1; fi
    fi
    exit "$load_status"
}
trap load_cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf 'Running %s test users in isolated project %s\n' "$REPGEN_LOAD_USERS" "$load_project"
load_compose up --build --abort-on-container-exit --exit-code-from load-check
