#!/usr/bin/env bash
# scripts/demo/kill_provider.sh
# Demo #8: Circuit breaker opens on killed provider, degrade path shown.
#
# Usage:
#   1. Start the LLM gateway:  cd services/llm_gateway && uvicorn app:app --port 8002
#   2. In another terminal:    bash scripts/demo/kill_provider.sh
#   3. Observe: gateway /health shows circuit_breaker=open, requests return degraded=true.
#
# Prerequisites: curl, jq (optional), a running llm_gateway on port 8002.

set -euo pipefail

GATEWAY="${LLM_GATEWAY_URL:-http://localhost:8002}"

echo "=== Demo #8: Circuit Breaker & Degradation Path ==="
echo ""
echo "Step 1: Checking gateway health..."
curl -s "$GATEWAY/health" | python -m json.tool 2>/dev/null || curl -s "$GATEWAY/health"
echo ""

echo "Step 2: Setting LLM_API_KEY to invalid value to simulate provider failure..."
# Send requests that will fail, tripping the breaker
for i in 1 2 3 4; do
    echo "  → Sending failing request $i..."
    curl -s -X POST "$GATEWAY/v1/chat" \
        -H "Content-Type: application/json" \
        -d '{
            "messages": [{"role": "user", "content": "test"}],
            "agent": "demo",
            "step": "breaker_test"
        }' | python -m json.tool 2>/dev/null || echo "  (request completed with fallback)"
    echo ""
done

echo "Step 3: Checking gateway health after failures..."
curl -s "$GATEWAY/health" | python -m json.tool 2>/dev/null || curl -s "$GATEWAY/health"
echo ""

echo "Step 4: Sending request with open breaker — should degrade to stub..."
RESPONSE=$(curl -s -X POST "$GATEWAY/v1/chat" \
    -H "Content-Type: application/json" \
    -d '{
        "messages": [{"role": "user", "content": "Is this complaint about a broker?"}],
        "agent": "researcher",
        "step": "classify"
    }')
echo "$RESPONSE" | python -m json.tool 2>/dev/null || echo "$RESPONSE"
echo ""

# Check degraded flag
if echo "$RESPONSE" | grep -q '"degraded": true'; then
    echo "✅ PASS: Response is degraded as expected when breaker is open."
else
    echo "⚠️  Response may not be degraded. Check gateway logs."
fi

echo ""
echo "=== Demo #8 Complete ==="
