#!/usr/bin/env bash
# Pull the latest code and (re)start the RunCore stack on the VM.
# Run from the repo root on the EC2 instance:  ./deploy/deploy.sh
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Pulling latest from git"
git pull --ff-only

echo "==> Building & starting containers"
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build

echo "==> Waiting for health"
for i in $(seq 1 30); do
  if docker compose -f deploy/docker-compose.yml exec -T app \
      python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8765')+'/health',timeout=4)" 2>/dev/null; then
    echo "==> Healthy ✔"
    break
  fi
  sleep 2
done

echo "==> Pruning old images"
docker image prune -f >/dev/null 2>&1 || true
echo "==> Done. Logs: docker compose -f deploy/docker-compose.yml logs -f"
