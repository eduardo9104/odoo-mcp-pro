#!/usr/bin/env bash
# Zero-downtime blue-green deploy for odoo-mcp-pro.
# Usage: ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.multi-tenant.yml"

# Determine which slot is currently running
if docker inspect mcp-blue --format '{{.State.Running}}' 2>/dev/null | grep -q true; then
    OLD=mcp-blue
    NEW=mcp-green
else
    OLD=mcp-green
    NEW=mcp-blue
fi

echo "==> Current: $OLD → deploying: $NEW"

# Pull latest code
echo "==> Pulling latest code..."
cd .. && git pull && cd deploy

# Build the new image
echo "==> Building image..."
$COMPOSE build --no-cache $NEW

# Start the new container
echo "==> Starting $NEW..."
$COMPOSE up -d $NEW

# Wait for healthy
echo "==> Waiting for $NEW to be healthy..."
for i in $(seq 1 30); do
    STATUS=$(docker inspect --format '{{.State.Health.Status}}' $NEW 2>/dev/null || echo "starting")
    if [ "$STATUS" = "healthy" ]; then
        echo "==> $NEW is healthy!"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "==> ERROR: $NEW did not become healthy in 30s, rolling back"
        $COMPOSE stop $NEW
        exit 1
    fi
    sleep 1
done

# Remove the old container (stop + rm) so Caddy DNS doesn't try to resolve it
echo "==> Removing $OLD..."
$COMPOSE rm -f -s $OLD

echo "==> Deploy complete: $NEW is live"
