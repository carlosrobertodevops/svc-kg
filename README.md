
# svc-kg

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





Micro-serviço FastAPI para exibir grafos (pyVis) a partir de RPC no Supabase.



## Endpoints

- `GET /health` → `?deep=1` para verificar conexão com Supabase
- `GET /graph/membros` → JSON (nodes/edges) com meta
- `GET /graph/membros/vis` → HTML pyVis
- `POST /rpc/get_graph_membros` → debug pass-through

### Parâmetros comuns
- `p_faccao_id`: string
- `p_include_co`: bool
- `p_max_pairs`: int
- `depth`: int
- `preview`: bool (default: true) – aplica truncamento
- `max_nodes`: int (default: 500)
- `max_edges`: int (default: 1000)
- `nocache`: bool – ignora cache do servidor
- `cache_ttl`: int – TTL customizado (segundos)







## Exemplo cURL

---
```bash
curl "https://svc-kg.SEUDOMINIO/graph/membros?p_faccao_id=abc123&preview=true"
curl "https://svc-kg.SEUDOMINIO/graph/membros/vis?p_faccao_id=abc123&physics=true"
curl -X POST "https://svc-kg.SEUDOMINIO/rpc/get_graph_membros" \
  -H "Content-Type: application/json" \
  -d '{"p_faccao_id":"abc123","p_include_co":true,"p_max_pairs":200}'

# Documentação

- Swagger UI: `GET /docs`
- Redoc: `GET /redoc`
- OpenAPI JSON gerado: `GET /openapi.json`
- OpenAPI YAML estático: `GET /openapi.yaml`

Endpoints principais:
- `GET /graph/members` → JSON (nodes/edges) para FlutterFlow
- `GET /graph/members/html` → Página HTML pyVis pronta para embed
- `GET /health` → status

```
---

# svc-kg

Microserviço de **Knowledge Graph** (membros, funções, facções) com **FastAPI**.

- Docs: **Swagger** `/docs`, **ReDoc** `/redoc`, **OpenAPI** `/openapi.json`
- Performance: **ORJSON**, **GZip**, **psycopg_pool** (async), **cache TTL**, **multi-workers**
- Preview seguro no navegador: `max_nodes` e `max_edges` (evita travar o Swagger)

## Rodar local (Postgres + serviço)

```bash
docker compose -f docker-compose.local.yml up --build

