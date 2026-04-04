#!/usr/bin/env bash
set -euo pipefail

# Deploy script: builds image locally, uploads to remote, runs via docker-compose.

REMOTE_HOST="test.k3rnel-pan1c.com"
REMOTE_PORT=2223
REMOTE_USER="marko"
IMAGE_NAME="search"
REMOTE="$REMOTE_USER@$REMOTE_HOST"
REMOTE_DIR="\$HOME/search"
SSH_OPTS=(-p "$REMOTE_PORT" -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
SCP_OPTS=(-P "$REMOTE_PORT" -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=6)
: "${PUBLIC_URL:?PUBLIC_URL must be set}"

# Retry wrapper: retry_cmd <max_attempts> <backoff_secs> <command...>
retry_cmd() {
  local max=$1 backoff=$2; shift 2
  local attempt=1
  while true; do
    if "$@"; then return 0; fi
    if (( attempt >= max )); then return 1; fi
    echo " (attempt $attempt/$max failed, retrying in ${backoff}s...)"
    sleep "$backoff"
    backoff=$(( backoff * 2 ))
    attempt=$(( attempt + 1 ))
  done
}
: "${LLM_BASE_URL:?LLM_BASE_URL must be set}"
: "${LLM_API_KEY:?LLM_API_KEY must be set}"
: "${API_KEY:?API_KEY must be set}"
: "${PORT:?PORT must be set}"
: "${AUTH_PASSWORD:?AUTH_PASSWORD must be set}"

print_remote_diagnostics() {
  echo "    remote diagnostics:"
  ssh "${SSH_OPTS[@]}" "$REMOTE" '
    cd ~/search 2>/dev/null || true
    docker compose ps 2>/dev/null || true
    echo
    docker compose logs --tail 40 search 2>/dev/null || true
  ' || true
}

# --- Build ---
printf "==> building image ($IMAGE_NAME, linux/amd64)..."
if [ "${SKIP_DOCKER_BUILD:-0}" != "1" ]; then
  ./scripts/build.sh linux/amd64 > /dev/null 2>&1
fi
echo "ok"

# --- Save & upload ---
printf "==> saving image..."
docker save "$IMAGE_NAME" | gzip > /tmp/"${IMAGE_NAME}".tar.gz
echo "ok"

printf "==> uploading to $REMOTE_HOST..."
retry_cmd 3 2 scp "${SCP_OPTS[@]}" /tmp/"${IMAGE_NAME}".tar.gz "$REMOTE":/tmp/"${IMAGE_NAME}".tar.gz
rm /tmp/"${IMAGE_NAME}".tar.gz
echo "ok"

# --- Load image on remote ---
printf "==> loading image on remote..."
ssh "${SSH_OPTS[@]}" "$REMOTE" '
  docker load < /tmp/'"${IMAGE_NAME}"'.tar.gz
  rm /tmp/'"${IMAGE_NAME}"'.tar.gz
' > /dev/null 2>&1
echo "ok"

# --- Upload docker-compose.yml ---
printf "==> uploading compose file..."
retry_cmd 3 2 ssh "${SSH_OPTS[@]}" "$REMOTE" "mkdir -p ~/search"
retry_cmd 3 2 scp "${SCP_OPTS[@]}" docker-compose.yml "$REMOTE":~/search/docker-compose.yml
echo "ok"

# --- Write .env on remote ---
printf "==> writing remote .env..."
printf -v port_q '%q' "$PORT"
printf -v llm_base_url_q '%q' "$LLM_BASE_URL"
printf -v llm_api_key_q '%q' "$LLM_API_KEY"
printf -v api_key_q '%q' "$API_KEY"
printf -v auth_password_q '%q' "$AUTH_PASSWORD"
retry_cmd 3 2 ssh "${SSH_OPTS[@]}" "$REMOTE" 'bash -se' <<EOF
cat > ~/search/.env <<'ENVEOF'
PORT=$port_q
LLM_BASE_URL=$llm_base_url_q
LLM_API_KEY=$llm_api_key_q
API_KEY=$api_key_q
AUTH_MODE=password
AUTH_PASSWORD=$auth_password_q
ENVEOF
EOF
echo "ok"

# --- Start services ---
printf "==> starting services..."
if ! retry_cmd 3 4 ssh "${SSH_OPTS[@]}" "$REMOTE" '
  cd ~/search
  docker compose up -d --remove-orphans
'; then
  echo "FAIL"
  print_remote_diagnostics
  exit 1
fi
echo "ok"

# --- Wait for server ---
printf "==> waiting for server..."
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-120}"
WAIT_INTERVAL_SECONDS="${WAIT_INTERVAL_SECONDS:-2}"
WAIT_DEADLINE=$(( $(date +%s) + WAIT_TIMEOUT_SECONDS ))
server_ready=false

while (( $(date +%s) < WAIT_DEADLINE )); do
  if ssh "${SSH_OPTS[@]}" "$REMOTE" 'curl -sf --max-time 3 http://localhost:'"$PORT"' > /dev/null' 2>/dev/null; then
    server_ready=true
    break
  fi
  sleep "$WAIT_INTERVAL_SECONDS"
done

if [[ "$server_ready" != true ]]; then
  echo "FAIL"
  echo "    server did not start within ${WAIT_TIMEOUT_SECONDS}s"
  print_remote_diagnostics
  exit 1
fi
echo "server reachable"

# --- Check public endpoint ---
printf "==> checking public endpoint ($PUBLIC_URL)..."
if ! body=$(curl -sfL --max-time 10 "$PUBLIC_URL"); then
  echo "FAIL"
  echo "    could not reach $PUBLIC_URL"
  exit 1
fi

if ! echo "$body" | grep -qE "Search|hello|Sign in"; then
  echo "FAIL"
  echo "    $PUBLIC_URL response did not look right"
  echo "    $body"
  exit 1
fi
echo "ok"

# --- Fetch logs ---
./scripts/get_logs.sh

echo "==> deployed $IMAGE_NAME to $PUBLIC_URL"
