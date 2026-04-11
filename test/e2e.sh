#!/usr/bin/env bash
set -euo pipefail

: "${PORT:?PORT must be set}"

# The e2e test only hits `GET /` on the search container, so it never actually
# exercises the LLM or any API_KEY-protected endpoint. The container reads
# LLM_BASE_URL at import time though, and docker-compose substitutes these
# vars unconditionally — so they need to be *set* (not necessarily real). When
# not provided by the caller, fall back to throwaway values so the e2e can
# run without any secrets at all (e.g. in CI).
: "${LLM_BASE_URL:=http://llm.invalid}"
: "${LLM_API_KEY:=e2e-dummy}"
: "${API_KEY:=$(head -c 24 /dev/urandom | base64 | tr -d '/+=\n')}"
export LLM_BASE_URL LLM_API_KEY API_KEY

if [ "${SKIP_DOCKER_BUILD:-0}" != "1" ]; then
  ./scripts/build.sh
fi
docker compose up -d
trap 'docker compose down' EXIT

echo "waiting for server..."
wait_timeout_seconds=120
wait_interval_seconds=2
deadline=$((SECONDS + wait_timeout_seconds))
attempt=0
last_status=""

while (( SECONDS < deadline )); do
  attempt=$((attempt + 1))
  # Accept 200 (no auth) or 302 (redirect to login) as "server is up"
  status=$(curl -sS -o /dev/null -w "%{http_code}" "http://localhost:${PORT}" || true)
  last_status="$status"

  if [ "$status" = "200" ] || [ "$status" = "302" ]; then
    echo "server is up (attempt ${attempt}, HTTP ${status})"
    break
  fi

  if [[ "$status" == 5* ]]; then
    echo "FAIL: server returned HTTP ${status} while starting (attempt ${attempt})"
    docker compose logs --tail 50 || true
    exit 1
  fi

  if [ -z "$status" ] || [ "$status" = "000" ]; then
    echo "waiting... attempt ${attempt}/${wait_timeout_seconds}s (server not reachable yet)"
  else
    echo "waiting... attempt ${attempt}/${wait_timeout_seconds}s (HTTP ${status})"
  fi

  sleep "$wait_interval_seconds"
done

if [ "$last_status" != "200" ] && [ "$last_status" != "302" ]; then
  echo "FAIL: server did not become ready within ${wait_timeout_seconds}s (last status: ${last_status:-none})"
  docker compose logs --tail 50 || true
  exit 1
fi

echo "checking response..."
# Follow redirects to get final page (login or index)
body=$(curl -sfL http://localhost:"$PORT")

if ! echo "$body" | grep -qE "Search|hello|Sign in"; then
  echo "FAIL: response does not contain expected content"
  echo "$body"
  exit 1
fi

echo "e2e test passed"
