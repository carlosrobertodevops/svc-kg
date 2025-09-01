#!/usr/bin/env python3
import os, sys, json, requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")

def must(cond, msg):
    if not cond: 
        print("❌", msg); sys.exit(1)
    print("✅", msg)

r = requests.get(f"{BASE_URL}/health", timeout=10)
must(r.status_code == 200, "health 200")

r = requests.get(f"{BASE_URL}/openapi.json", timeout=10)
must(r.ok and "paths" in r.json(), "openapi ok")

r = requests.get(f"{BASE_URL}/v1/graph/membros?include_co=true", timeout=30)
must(r.ok, "graph 200")
data = r.json()
must(isinstance(data.get("nodes"), list), "nodes é lista")
must(isinstance(data.get("edges"), list), "edges é lista")

# checa campos mínimos
if data["nodes"]:
    n0 = data["nodes"][0]
    must(all(k in n0 for k in ("id","label","type")), "node tem id/label/type")

if data["edges"]:
    e0 = data["edges"][0]
    must(all(k in e0 for k in ("source","target")), "edge tem source/target")

print(f"🎉 Testes OK em {BASE_URL} com {len(data['nodes'])} nós e {len(data['edges'])} arestas")
