
# svc-kg (v1.7.17)

svc-kg/
├─ db/
│  ├─ 00-roles.sql    # schema + seed + get_graph_membros
│  ├─ 01-indexes.sql # índices
│  ├─ 02-tables.sql # índices
│  ├─ 03.function.sql # índices
│  └─ 04-seed.sql   # et_graph_membros -> get_graph_membros
├─ docs/
│  └─ openapi.yaml   # Swagger spec estático (usado no /docs)
├─ static/           # (montado no container)
│   ├─ vis-page.js
│   └─ vis-style.css
├─ app.py
├─ Dockerfile
├─ docker-compose.local.yml
├─ docker-compose.coolify.yml
├─ .env.example
├─ .gitignore
├─ .dockerignore
├─ README.md



Microserviço de **Knowledge Graph** (membros, facções, funções) com:
- Backend: **Supabase RPC** (`get_graph_membros`) **ou** Postgres.
- Cache: Redis (fallback em memória).
- Visualização:
  - `/v1/vis/pyvis` → **PyVis** (usa **inline JS**; pode requerer CSP relaxada)
  - `/v1/vis/visjs` → **vis-network** (sem inline; compatível com CSP rígida)

Microserviço de **Knowledge Graph** com:
- Backend: **Supabase RPC** (`get_graph_membros`) ou **Postgres**.
- Cache: **Redis** (fallback em memória).
- Visualização:
  - `/v1/vis/pyvis` → **PyVis** (usa inline JS; pode ser bloqueado por CSP rígida)
  - `/v1/vis/visjs` → **vis-network** (sem inline; **assets locais**, compatível com CSP)

## Endpoints

- `GET /live` — liveness  
- `GET /ready` — readiness (DNS/Redis/backend)  
- `GET /v1/graph/membros` — JSON `{nodes, edges}`  
- `GET /v1/nodes/{id}/neighbors` — subgrafo (raio 1)  
- Visualização:
  - `GET /v1/vis/pyvis?...`
  - `GET /v1/vis/visjs?...`
- OpenAPI: `docs/openapi.yaml`

## Rodando LOCAL (Postgres + Redis)

1. Crie `.env` a partir de `.env.example` e defina:
---
---
---
---
```env
   APP_ENV=development
   PORT=8080
   WORKERS=2
   LOG_LEVEL=debug

   # Local usa Postgres (não Supabase)
   DATABASE_URL=postgresql://kg:kg@db:5432/kg
   SUPABASE_URL=
   SUPABASE_SERVICE_KEY=

```
---
