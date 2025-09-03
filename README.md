
# svc-kg (v1.7.5)

svc-kg/
├─ app.py
├─ Dockerfile
├─ docker-compose.local.yml
├─ docker-compose.coolify.yml
├─ .env.example
├─ .gitignore
├─ .dockerignore
├─ README.md
├─ assets/           # (montado no container)
│  └─ .keep
├─ static/           # (montado no container)
│  └─ .keep
├─ tmp/              # (montado no container)
│  └─ .gitkeep
├─ docs/
│  └─ openapi.yaml   # Swagger spec estático (usado no /docs)
└─ db/
   ├─ 00_init.sql    # schema + seed + get_graph_membros
   ├─ 01_indexes.sql # índices
   └─ 02_alias.sql   # et_graph_membros -> get_graph_membros

Microserviço de **Knowledge Graph** (membros, facções, funções) com:
- Backend: **Supabase RPC** (`get_graph_membros`) **ou** Postgres.
- Cache: Redis (fallback em memória).
- Visualização:
  - `/v1/vis/pyvis` → **PyVis** (usa **inline JS**; pode requerer CSP relaxada)
  - `/v1/vis/visjs` → **vis-network** (sem inline; compatível com CSP rígida)

## Endpoints principais

- `GET /live` — liveness
- `GET /ready` — readiness (DNS/Redis/backend)
- `GET /v1/graph/membros` — JSON `{ nodes, edges }`
- `GET /v1/vis/pyvis` — HTML PyVis (inline JS)
- `GET /v1/vis/visjs` — HTML vis-network (zero inline JS)
- `GET /v1/nodes/{node_id}/neighbors` — subgrafo (raio 1)
- Swagger: `docs/openapi.yaml`

Parâmetros comuns:  
`faccao_id` (opcional), `include_co` (bool), `max_pairs`, `max_nodes`, `max_edges`, `cache`.



## Como rodar (local)

1. Crie `.env` a partir de `.env.example`. Para **local** use Postgres:
---
```env
   DATABASE_URL=postgresql://kg:kg@db:5432/kg
   SUPABASE_URL=
   SUPABASE_SERVICE_KEY=
# svc-kg

```
---