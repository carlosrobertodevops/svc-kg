#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"

pass() { echo -e "✅ $1"; }
fail() { echo -e "❌ $1"; exit 1; }

jq --version >/dev/null 2>&1 || { echo "Instale jq"; exit 1; }

# Health
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/health")
[[ "$code" == "200" ]] && pass "Health 200" || fail "Health $code"

# OpenAPI
len=$(curl -s "$BASE_URL/openapi.json" | jq '.paths|keys|length')
[[ "$len" -ge 2 ]] && pass "OpenAPI ok ($len rotas)" || fail "OpenAPI inválido"

# Graph (preview)
resp=$(curl -s "${BASE_URL}/v1/graph/membros?include_co=true")
nodes=$(echo "$resp" | jq '.nodes|length')
edges=$(echo "$resp" | jq '.edges|length')
[[ "$nodes" =~ ^[0-9]+$ && "$edges" =~ ^[0-9]+$ ]] || fail "Graph sem nodes/edges"
pass "Graph ok ($nodes nós / $edges arestas)"

# Vizinho (se houver id=1)
code=$(curl -s -o /dev/null -w '%{http_code}' "${BASE_URL}/v1/nodes/1/neighbors")
[[ "$code" == "200" ]] && pass "Neighbors 200" || pass "Neighbors ignorado (id=1 pode não existir)"

echo "🎉 Smoke tests concluídos em $BASE_URL"
