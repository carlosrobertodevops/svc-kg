# DOCKER.md — svc-kg (v1.8.0)

Guia de containerização do microserviço **svc-kg** (Knowledge Graph do produto
**mondaha**). Cobre os `docker-compose*`, o `Dockerfile`, comandos por cenário,
a integração com o stack **mondaha** e o scrape de métricas pela observability.

> Contexto: `svc-kg` = **Python 3.11 / FastAPI**, servido por **Uvicorn (dev)**
> ou **Gunicorn + UvicornWorker (prod)**. Backend **Postgres direto** do stack
> mondaha via **psycopg 3** (Supabase/PostgREST **removido**). Cache **Redis**
> (fail-soft em memória). Repositório separado; branch de trabalho `svc-kg-fs`.

---

## 1. Comparativo dos composes

O repo tem **quatro** arquivos de orquestração, para cenários distintos:

| Arquivo | Versão | Propósito | Serviços | Porta svc-kg | Backend de dados | Rede | Healthcheck |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `docker-compose.yaml` | v1.8.0 | **Deploy principal (Coolify)** — migrado p/ Postgres direto | `redis` (7-alpine) + `svc-kg` | não publica porta (via proxy Coolify) | `DATABASE_URL` (Postgres mondaha, vindo do `.env`/SSM) + Redis local | `obs_net` (external) | `GET /live` |
| `docker-compose.mondaha.yml` | v1.8.0 | **Acoplar ao stack mondaha já em execução** (só o svc-kg) | apenas `svc-kg` | `8080:8080` | Postgres + Redis do mondaha (in-network) | `mondaha_default` (external) | `GET /health` |
| `docker-compose.local.yaml` | v1.7.20 | **Local legado** — emula Supabase via PostgREST (INALTERADO) | `db` (postgres:15) + `postgrest` (v12.2.3) + `redis` + `svc-kg` | `8080:8080` | PostgREST/Supabase (`SUPABASE_*`) | default (bridge) | `GET /health` |
| `docker-compose.coolify.yml` | v1.7.20 | **Coolify legado** — ainda via `SUPABASE_*` (INALTERADO) | `redis` + `svc-kg` | não publica porta | Supabase (`SUPABASE_URL`/`SUPABASE_SERVICE_KEY`) | default (bridge) | `GET /live` |

Notas de fidelidade aos arquivos:

- **`docker-compose.yaml`** e **`docker-compose.mondaha.yml`** são os arquivos
  **atuais (v1.8.0)**: consomem o **Postgres do mondaha** via `DATABASE_URL`.
- **`docker-compose.local.yaml`** e **`docker-compose.coolify.yml`** são
  **legados (v1.7.20)**: ainda usam `SUPABASE_*`. Segundo o `CLAUDE.md`/README,
  Supabase está **deprecado** — prefira os dois primeiros.
- Em `docker-compose.yaml`, o comentário do arquivo documenta o DSN in-network
  do mondaha como `postgresql://mondaha:mondaha@postgres:5432/mondaha`.
- Todos os composes com Redis usam `redis:7-alpine` com
  `command: ["redis-server", "--save", "", "--appendonly", "no"]` (sem
  persistência) e `restart: unless-stopped`.

---

## 2. Dockerfile

`Dockerfile` (v1.7.20, marcado como **INALTERADO**), **single-stage**:

| Item | Valor |
| --- | --- |
| Base image | `python:3.11-slim` |
| ENV base | `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1` |
| Pacotes SO | `curl`, `ca-certificates` (via `apt-get`) |
| Deps Python (pin) | `fastapi==0.115.0`, `uvicorn[standard]==0.30.6`, `gunicorn==22.0.0`, `psycopg[binary]==3.2.1`, `psycopg_pool==3.2.1`, `orjson==3.10.7`, `httpx==0.27.2`, `redis==5.0.7`, `PyYAML==6.0.2`, `networkx==3.3`, `pyvis==0.3.2`, `prometheus-fastapi-instrumentator==6.1.0` |
| WORKDIR | `/app` |
| Copiado | `app.py`, `db_pg.py`, `db/`, `static/`, `docs/` |
| Assets | Baixa `vis-network@9.1.6` (js + css) de `unpkg.com` para `/app/static/vendor/` no build (sem CDN em runtime) |
| Porta | `EXPOSE 8080` |
| ENV default | `PORT=8080`, `WORKERS=2`, `LOG_LEVEL=info`, `SERVER_CMD=gunicorn` |

**Comando de start** (via `CMD ["bash","-lc", ...]`) — seleciona o servidor por
`SERVER_CMD`:

```bash
# SERVER_CMD=uvicorn  (dev)
uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --log-level ${LOG_LEVEL:-info}

# SERVER_CMD=gunicorn (default / prod)
gunicorn -w ${WORKERS:-2} -k uvicorn.workers.UvicornWorker app:app \
  -b 0.0.0.0:${PORT:-8080} --timeout 120 --graceful-timeout 120 \
  --log-level ${LOG_LEVEL:-info}
```

> **Timeout do Gunicorn:** `--timeout 120 --graceful-timeout 120` (antes era
> `--timeout 60`, sem graceful). Render de grafos grandes (~2000 nós) no
> `/v1/vis/pyvis` pode passar de 60s; a montagem PyVis agora roda **fora do
> event loop** (`asyncio.to_thread`), mas o teto de 120s evita o worker ser
> morto por timeout durante a estabilização. Aplicado tanto no `CMD` do
> Dockerfile quanto no `start.sh`.

> `start.sh` existe como entrypoint alternativo (auto-detecta `app:app` ou
> `src.app:app`, roda Gunicorn + UvicornWorker em `:8080` com o mesmo
> `--timeout 120 --graceful-timeout 120`), mas o `CMD` padrão do Dockerfile
> **não** o invoca — usa a linha `bash -lc` acima.

`prometheus-fastapi-instrumentator` é o que expõe o endpoint **`/metrics`**
(ver §5).

---

## 3. Build & Run por cenário

### 3.1. Acoplado ao stack mondaha (recomendado)

Requer o stack **mondaha** já no ar (serviços `postgres` = postgis:17-3.5 e
`redis` = 7-alpine), com a rede `mondaha_default` existente:

```bash
# no diretório do svc-kg
docker compose -f docker-compose.mondaha.yml up --build
```

- Sobe **somente** o `svc-kg` (imagem `svc-kg:mondaha`, container `svc-kg`).
- Conecta na rede external `mondaha_default`.
- `DATABASE_URL=postgresql://mondaha:mondaha@postgres:5432/mondaha`,
  `REDIS_URL=redis://redis:6379/0`, `KG_AUTO_MIGRATE=true`.
- Publica `8080:8080`; healthcheck `GET /health`.

Se você subiu o stack mondaha com `-p <projeto>`, a rede muda para
`<projeto>_default` — ajuste `networks.mondaha.name` no compose. Confirme com
`docker network ls`.

### 3.2. Deploy principal (Coolify, com Redis próprio)

```bash
docker compose up --build           # usa docker-compose.yaml
```

- Sobe `redis` + `svc-kg` na rede external **`obs_net`**.
- `DATABASE_URL` vem do `.env` / SSM (Postgres do mondaha).
- Redis é local ao compose (`redis://redis:6379/0`).
- svc-kg **não publica porta** (roteado pelo proxy do Coolify); healthcheck
  `GET /live`.

### 3.3. Local legado (emulação Supabase via PostgREST)

```bash
docker compose -f docker-compose.local.yaml up --build
```

- Sobe `db` (postgres:15, `:5432`), `postgrest` (`:3000`), `redis`, `svc-kg`
  (`:8080`).
- Usa `SUPABASE_*` (legado). Preferir os cenários 3.1/3.2 (Postgres direto).

### 3.4. Dev no host (sem Docker)

```bash
pip install -r requirements.txt
bash start.sh                                # gunicorn + UvicornWorker :8080
uvicorn app:app --reload --port 8080         # dev com reload
```

Para rodar o svc-kg **fora** do Docker apontando ao Postgres do mondaha, troque
o host do DSN para `localhost` (ver comentário no `.env.exemple`).

---

## 4. Integração com o mondaha

```
Browser (tela "Conhecimento") ──iframe──▶ svc-kg  ──psycopg3──▶ Postgres (mondaha)
      NEXT_PUBLIC_KG_SERVICE_URL             :8080  └──redis───▶ Redis (cache)
                                              │
                        Prometheus ──scrape──▶ /metrics
```

- **DNS interno:** dentro da rede docker do mondaha, o serviço é alcançável em
  **`http://svc-kg:8080`** (nome do serviço `svc-kg` no
  `docker-compose.mondaha.yml`).
- **Consumo pelo app mondaha (BFF same-origin):** a tela **"Conhecimento"**
  **não** embute mais o svc-kg cross-origin no browser. O mondaha expõe uma rota
  BFF `GET /api/kg/pyvis` que faz `fetch` **server-side** em
  `http://svc-kg:8080/v1/vis/pyvis` (DNS interno docker) e serve o HTML
  **same-origin** (`text/html`, **sem** `X-Frame-Options` próprio). Evita
  "connection reset"/"refused to connect" por scheme (https→http) e por
  `X-Frame-Options`. Params repassados: `faccao_id`, `include_co`, `max_pairs`,
  `max_nodes`, `max_edges`, `cache`, `theme`, `title`. Rotas de visualização:
  - `GET /v1/vis/pyvis?...` → **PyVis** (JS inline; pode esbarrar em CSP rígida).
    Montagem do HTML roda **fora do event loop** (`asyncio.to_thread`); layout
    afinado (`forceAtlas2Based` + `improvedLayout:false` + `drawThreshold`) para
    grafos grandes não virarem "hairball".
  - `GET /v1/vis/visjs?...` → **vis-network** (assets locais, compatível com CSP).
- **Dependência do Postgres do mondaha:** o **único** acesso a dados é a função
  SQL `public.get_graph_membros(p_faccao_id bigint, p_include_co boolean,
  p_max_pairs int)`, que devolve um `jsonb` `{nodes, edges}`. Lê as tabelas
  `membros`, `faccoes`, `funcoes` do banco mondaha.
- **Migração idempotente no startup:** com `KG_AUTO_MIGRATE=true`,
  `ensure_schema()` (em `db_pg.py`) instala a função + índices de forma
  **idempotente** a partir do SQL de install no boot do container — não precisa
  de step manual de migração.
- **Cache Redis:** cacheia o grafo (`kg:graph:{faccao_id}:{include_co}:{max_pairs}`)
  e o **HTML renderizado** (`kg:html:...`). Fail-soft: erro de Redis/Postgres não
  derruba a rota.

---

## 5. Métricas / Observability

- O svc-kg expõe **`/metrics`** (via `prometheus-fastapi-instrumentator`) na
  mesma porta HTTP **`:8080`**.
- No `docker-compose.yaml` o serviço fica na rede external **`obs_net`**, de modo
  que o stack de observability (Prometheus) faça **scrape** em
  **`svc-kg:8080/metrics`**.
- **Não** duplicamos aqui a doc de Prometheus/Grafana/alertas — a configuração
  canônica de observability está em **`mondaha/docs/INFRA.md §16`**. Este guia só
  cobre o que o container do svc-kg expõe.

---

## 6. Variáveis de ambiente

Extraídas de `.env.exemple` e do `CLAUDE.md`/README:

| Var | Função | Exemplo / default |
| --- | --- | --- |
| `APP_ENV` | Ambiente | `production` \| `development` |
| `APP_HOST` | Host público (deploy) | `svc-kg.mondaha.com` |
| `PORT` | Porta HTTP | `8080` |
| `WORKERS` | Nº de workers Gunicorn | `2` (prod) / `1` (local) |
| `LOG_LEVEL` | Nível de log | `info` \| `debug` |
| `SERVER_CMD` | Servidor no `CMD` do Dockerfile | `gunicorn` (default) \| `uvicorn` |
| `DATABASE_URL` | DSN do Postgres do mondaha (psycopg 3) | `postgresql://mondaha:mondaha@postgres:5432/mondaha` |
| `PG_POOL_MAX` | Tamanho máximo do pool psycopg | `10` |
| `PG_POOL_TIMEOUT` | Timeout (s) p/ obter conexão do pool | `10` |
| `KG_AUTO_MIGRATE` | Instala `get_graph_membros` no startup (idempotente) | `true` |
| `REDIS_URL` | DSN do Redis | `redis://redis:6379/0` |
| `ENABLE_REDIS_CACHE` | Liga/desliga cache Redis | `true` |
| `CACHE_API_TTL` | TTL (s) do cache de grafo/HTML | `60` |
| `CACHE_STATIC_MAX_AGE` | `max-age` de estáticos | `86400` |
| `CORS_ALLOW_ORIGINS` | CORS: origens | `*` |
| `CORS_ALLOW_METHODS` | CORS: métodos | `GET,POST,OPTIONS` |
| `CORS_ALLOW_HEADERS` | CORS: headers | `Authorization,Content-Type` |
| `CORS_ALLOW_CREDENTIALS` | CORS: credenciais | `false` |
| `MEMBERS_TABLE` / `MEMBERS_ID_COL` / `MEMBERS_PHOTO_COL` | Config tabela de fotos | `membros` / `id` / `photo_url` |
| `COOLIFY_PROXY_NETWORK` | Rede do proxy (só se usar compose coolify-proxy) | `coolify-proxy` |
| `SUPABASE_*` | **DEPRECADO** — migrado p/ Postgres direto | (ignorado) |

Arquivos de env por cenário: `.env` (deploy Coolify / mondaha), `.env.local`
(compose local legado). Referência: `.env.exemple`.

---

## 7. Healthcheck & portas

- **Porta única:** `8080` (HTTP) — `EXPOSE 8080` no Dockerfile.
- **Healthchecks nos composes:**
  - `docker-compose.yaml` / `docker-compose.coolify.yml` → `curl -sf http://localhost:8080/live`.
  - `docker-compose.mondaha.yml` / `docker-compose.local.yaml` → `curl -sf http://localhost:8080/health`.
  - Parâmetros: `interval: 10s`, `timeout: 5s`, `retries: 5`.
- **Endpoints de saúde disponíveis:** `GET /live` (liveness), `GET /ready`
  (readiness — DNS/Redis/backend), `GET /health`.
- **`GET /live` é o healthcheck do deploy principal** (`docker-compose.yaml` /
  Coolify): responde barato e **independente** de render/DB, então o
  `--timeout 120` do Gunicorn (necessário para render de grafo grande no
  `/v1/vis/pyvis`) **não** atrasa o healthcheck — o `/live` continua rápido
  mesmo com um worker ocupado montando o HTML PyVis.
- **Redis** (nos composes que o sobem): healthcheck `redis-cli ping`.
- **Postgres** (`docker-compose.local.yaml`): healthcheck `pg_isready`.

---

## 8. Troubleshooting

| Sintoma | Causa provável | Correção |
| --- | --- | --- |
| `network mondaha_default not found` | Stack mondaha não está no ar, ou subiu com `-p <projeto>` (rede vira `<projeto>_default`) | Suba o stack mondaha primeiro; ajuste `networks.mondaha.name` no `docker-compose.mondaha.yml`; confira com `docker network ls` |
| `network obs_net not found` (deploy principal) | Rede external de observability inexistente | Crie/garanta a rede `obs_net` antes do `docker compose up` (ver `mondaha/docs/INFRA.md §16`) |
| Conexão ao Postgres falha in-network | `DATABASE_URL` com host errado | Use host = serviço `postgres` (`...@postgres:5432/mondaha`) dentro da rede; use `localhost` só ao rodar o svc-kg **fora** do Docker |
| Grafo vazio / função ausente no banco | Migração idempotente não rodou | Garanta `KG_AUTO_MIGRATE=true`; `ensure_schema()` instala `public.get_graph_membros` no startup |
| PyVis não renderiza no iframe (CSP) | PyVis usa JS inline | Use a rota `/v1/vis/visjs` (assets locais, compatível com CSP) |
| Prometheus não coleta métricas | svc-kg fora da rede `obs_net` | Deploy via `docker-compose.yaml` (rede `obs_net`); scrape em `svc-kg:8080/metrics` — ver `mondaha/docs/INFRA.md §16` |
| Reintrodução acidental de Supabase | Uso dos composes legados (`local`/`coolify`) | Preferir `docker-compose.yaml` / `docker-compose.mondaha.yml`; `SUPABASE_*` está deprecado |

---

## 9. Referências

- `README.md` — visão geral, endpoints, env vars (v1.8.0).
- `CLAUDE.md` — regras operacionais, stack, arquitetura, contrato de rotas.
- `CHANGELOG.md` — histórico de versões.
- `Dockerfile`, `docker-compose.yaml`, `docker-compose.mondaha.yml`,
  `docker-compose.local.yaml`, `docker-compose.coolify.yml`, `.env.exemple`.
- **`mondaha/docs/INFRA.md §16`** — observability (Prometheus/Grafana/scrape),
  não duplicada aqui.
