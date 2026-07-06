
# svc-kg (v1.8.0)
- Data atualização: 06/07/2026
---
```
svc-kg/
├─ .vscode/
│  ├─ {} settings.json
├─ db/
│  ├─ 00_init.sql    	# schema + seed + get_graph_membros
│  ├─ 01_indexes.sql 	# índices
│  └─ 02_alias.sql   	# et_graph_membros -> get_graph_membros
├─ docs/
│  └─ openapi.yaml   	# Swagger spec estático (usado no /docs)
├─ static/           	# (montado no container)
│   ├─ vis-embed.js		# Apoio do visjs
│   ├─ vis-page.js
│   └─ vis-style.css	# Apoio do visjs e do pyvis
├─ .dockerignore
├─ .editorconfig
├─ .env
├─ .env.example
├─ .gitignore
├─ app.py
├─ docker-compose.local.yml		# docker local
├─ docker-compose.coolify.yml	# docker coolify
├─ docker-compose.yml			# docker prinicpal para o coolify
├─ requirements.txt
├─ Dockerfile
├─ CHANGELOG.md
├─ README.md
├─ start.sh
├─ test_svc_kg.sh

```
---
Microserviço de **Knowledge Graph** com:
- Backend: **Postgres direto** do stack **mondaha** via **psycopg 3** (`SELECT public.get_graph_membros($1,$2,$3)`).
  - Módulo `db_pg.py` (pool async: `fetch_graph`, `pg_ping`, `ensure_schema`, `backend_ok`, `close_pool`).
  - A função SQL é instalada de forma **idempotente** no startup a partir de `db/mondaha_install.sql` (flag `KG_AUTO_MIGRATE=true`).
  - Tabelas lidas no mondaha: `membros(membro_id, nome_completo, alcunha varchar[], faccao_id, funcao_id)`, `faccoes(faccao_id, nome)`, `funcoes(funcao_id, nome, faccao_id)`.
  - ⚠️ **Supabase / PostgREST** foi **removido** — variáveis `SUPABASE_*` estão **deprecadas**.
- Cache: **Redis** (fallback em memória). Além do grafo (`kg:graph:{faccao_id}:{include_co}:{max_pairs}`), também cacheia o **HTML renderizado** (`kg:html:{rota}:...` com todos os params).
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

### 1. Crie `.env` a partir de `.env.example` e defina:

---
```env
   APP_ENV=development
   PORT=8080
   WORKERS=2
   LOG_LEVEL=debug

   # Postgres direto (não Supabase)
   DATABASE_URL=postgresql://kg:kg@db:5432/kg
   PG_POOL_MAX=10
   PG_POOL_TIMEOUT=10
   KG_AUTO_MIGRATE=true

   # Cache Redis
   REDIS_URL=redis://redis:6379/0
   ENABLE_REDIS_CACHE=true
   CACHE_API_TTL=60
		...

```
---

## Rodando contra o Postgres do mondaha

O serviço lê os dados **direto do Postgres do stack mondaha** e cacheia no Redis.
Com o `postgres` e o `redis` do mondaha no ar (rede externa `mondaha_default`):

```bash
docker compose -f docker-compose.mondaha.yml up --build
```

### Env vars principais

```env
DATABASE_URL=postgresql://mondaha:mondaha@postgres:5432/mondaha
REDIS_URL=redis://redis:6379/0
ENABLE_REDIS_CACHE=true
CACHE_API_TTL=60
KG_AUTO_MIGRATE=true      # instala public.get_graph_membros via db/mondaha_install.sql (idempotente)
PG_POOL_MAX=10
PG_POOL_TIMEOUT=10
```

> As variáveis `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` **não são mais usadas**.

