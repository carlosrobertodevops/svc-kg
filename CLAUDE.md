# CLAUDE.md — svc-kg

Regras operacionais para qualquer sessão de IA neste repositório. Leia antes de alterar código. Objetivo: trabalhar corretamente sem quebrar o serviço.

## 1. Visão geral

`svc-kg` é o microserviço de **Knowledge Graph** do produto **mondaha**. Serve o grafo de relações **membros / facções / funções** em dois formatos:

- **JSON** (`{nodes, edges}`) para consumo programático.
- **HTML** (visualização interativa) via **PyVis** e **vis.js (vis-network)** — embutido em `<iframe>` pela tela **"Conhecimento"** do app mondaha.

Repositório **separado** do monorepo mondaha. GitHub: `carlosrobertodevops/svc-kg`. Branch de trabalho: **`svc-kg-fs`**.

## 2. Stack

| Camada | Tecnologia |
| --- | --- |
| Linguagem | Python 3.11+ |
| Web framework | FastAPI 0.111 (imagem Docker usa 0.115) |
| Servidor | Uvicorn (dev) / Gunicorn + UvicornWorker (prod) |
| Visualização | PyVis 0.3.2 + vis-network (vis.js) — assets locais em `static/vendor/` |
| Cache | Redis (`redis` 5.x async) — fallback fail-soft em memória |
| Banco | **Postgres direto** (stack mondaha) via **psycopg 3** + `psycopg-pool` |

⚠️ **Sem Supabase.** A integração via Supabase/PostgREST/`httpx` foi **removida** na migração (Supabase → Postgres direto). As variáveis `SUPABASE_*` estão **deprecadas**.

## 3. Arquitetura (1 parágrafo)

`app.py` é **monolítico** (todas as rotas FastAPI + render HTML de PyVis e vis.js + helpers de cache/CSP). A camada de dados fica **isolada** em `db_pg.py`, que mantém um pool async psycopg 3 (singleton lazy) e expõe interface congelada: `fetch_graph`, `pg_ping`, `ensure_schema`, `backend_ok`, `close_pool`. O **único** acesso a dados é a função SQL `public.get_graph_membros(p_faccao_id bigint, p_include_co boolean, p_max_pairs int)`, que devolve um único `jsonb` `{nodes, edges}`. Redis cacheia tanto o grafo (`kg:graph:{faccao_id}:{include_co}:{max_pairs}`) quanto o **HTML renderizado** (`kg:html:...`). No startup, com `KG_AUTO_MIGRATE=true`, `ensure_schema()` instala a função + índices de forma **idempotente** a partir de `db/mondaha_install.sql`.

**Notas de correção (não regredir):**
- **Layout PyVis legível** (`GET /v1/vis/pyvis`): `net.set_options` usa `layout.improvedLayout:false` + solver `forceAtlas2Based` (`gravitationalConstant:-80`, `springLength:140`, `avoidOverlap:0.7`), `stabilization.iterations:900`, `nodes.scaling.label.drawThreshold` (rótulos só ao dar zoom), font com halo theme-aware e `edges.opacity:0.35`. **Não** voltar ao default sem solver + `improvedLayout` (trava >100 nós → "hairball" ilegível).
- **Render PyVis fora do event loop:** a montagem (`add_node`/`add_edge`/`set_options`/`generate_html`/`html.replace`) foi extraída para função **síncrona** chamada via `await asyncio.to_thread(...)`. Manter assim — bloquear o worker Uvicorn em grafos grandes (~2000 nós) faz o Gunicorn matar o worker por timeout.
- **`ensure_schema()` resiliente** (`db_pg.py`): executa em **AUTOCOMMIT** (`await conn.set_autocommit(True)`) mantendo o `pg_advisory_lock` (serializa os 2 workers), com `try/except` que loga o **erro real** do statement. Elimina o log de boot "current transaction is aborted" (race dos 2 workers aplicando o schema em transação única) e para de mascarar o erro. Idempotência do SQL preservada.
- **Tipografia Mondaha no toolbar PyVis** (`GET /v1/vis/pyvis`): o `toolbar_css` importa **Google Fonts** (Outfit + Plus Jakarta Sans) e aplica **Outfit** (700) no título "Knowledge Graph (PyVis)", **Plus Jakarta Sans** no input de busca e nos botões Imprimir/Recarregar, cores theme-aware (`bgcolor`/`fontcolor` por `theme`) e accent **#2B18EE** (primary Mondaha) no focus do input / hover dos botões; `set_options` usa `nodes.font.face = "Plus Jakarta Sans, Inter, Arial, sans-serif"`. **Não** regredir para fontes default. Fontes vêm da **CDN Google Fonts** (`fonts.googleapis.com` / `fonts.gstatic.com`) — o consumidor mondaha já libera essas origens em `style-src`/`font-src` da CSP do iframe.

**Fontes canônicas de detalhe:** `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/DATABASE.md`, `docs/OPS.md` (consultar antes de mudanças estruturais).

## 4. Gerado vs editável / cuidado

| Arquivo | Natureza | Cuidado |
| --- | --- | --- |
| `app.py` | Fonte principal (monolítico, ~40 KB) | Editar com atenção; concentra rotas + render HTML |
| `db_pg.py` | Camada de dados | Manter a interface pública congelada |
| `db/*.sql` | SQL (função `get_graph_membros`, índices, alias) | Idempotente; alterações vão ao Postgres do mondaha |
| `docs/openapi.yaml` | Spec OpenAPI estático servido em `/docs` | Manter em sincronia com as rotas |
| `static/vendor/*` | vis-network baixado no build | Não versionar/editar à mão |

**Regras firmes:**
- **NÃO** reintroduzir Supabase / PostgREST / `httpx` para acesso a dados (a migração já os removeu).
- Toda alteração vai na branch **`svc-kg-fs`**.
- **NUNCA** commitar sem permissão explícita do usuário.

## 5. Comandos

**Dev local (host):**
```bash
pip install -r requirements.txt
bash start.sh                                 # gunicorn + UvicornWorker :8080
uvicorn app:app --reload --port 8080          # dev com reload
python3 -m py_compile app.py db_pg.py         # sanity de sintaxe
```

**Docker contra o stack mondaha** (requer `postgres` + `redis` do mondaha no ar, rede externa `mondaha_default`):
```bash
docker compose -f docker-compose.mondaha.yml up --build
```

**Testes (smoke):**
```bash
bash test_svc_kg.sh                           # health, openapi, graph, neighbors
```

## 6. Variáveis de ambiente principais

| Var | Função |
| --- | --- |
| `DATABASE_URL` | DSN do Postgres do mondaha (ex: `postgresql://mondaha:mondaha@postgres:5432/mondaha`) |
| `REDIS_URL` | DSN do Redis (ex: `redis://redis:6379/0`) |
| `ENABLE_REDIS_CACHE` | Liga/desliga cache Redis |
| `CACHE_API_TTL` | TTL (s) do cache de grafo/HTML |
| `KG_AUTO_MIGRATE` | Instala `get_graph_membros` no startup (idempotente) |
| `PG_POOL_MAX` | Tamanho máximo do pool psycopg |
| `PG_POOL_TIMEOUT` | Timeout (s) para obter conexão do pool |
| `PORT` | Porta HTTP (default 8080) |
| `CORS_ALLOW_ORIGINS` / `CORS_ALLOW_METHODS` / `CORS_ALLOW_HEADERS` / `CORS_ALLOW_CREDENTIALS` | Config CORS |

`SUPABASE_*` = **deprecadas** (ignoradas). Detalhe completo em `.env.exemple` / `docs/OPS.md`.

## 7. Rotas

`/live`, `/health`, `/ready`, `/ops/status`, `/v1/graph/membros`, `/v1/vis/visjs`, `/v1/vis/pyvis`, `/docs`.

Detalhe (params, respostas) em `docs/API.md` e `docs/openapi.yaml`.

## 8. Convenções

- Código **Python async idiomático**: `await` em toda I/O (DB, Redis).
- **Fail-soft** em cache e DB: erro de Redis ou Postgres **não** derruba a rota — loga e degrada.
- **Nunca logar credenciais**: mascarar o DSN (ver `_mask_dsn` / `_mask_db_url`).
- **Preservar o contrato das rotas**: paths, params e formato de resposta (`{nodes, edges}`) são consumidos pelo app mondaha — não quebrar.
- **Consumo via BFF same-origin:** o mondaha **não** embute mais o svc-kg cross-origin no browser; ele faz `fetch` **server-side** de `http://svc-kg:8080/v1/vis/pyvis` (DNS interno docker) numa rota BFF `GET /api/kg/pyvis` e serve o HTML same-origin. O `/v1/vis/pyvis` deve continuar retornando **`text/html` sem `X-Frame-Options` próprio** e aceitando os params `faccao_id`, `include_co`, `max_pairs`, `max_nodes`, `max_edges`, `cache`, `theme`, `title`.

## 9. MCP / ferramentas

- Usar **Context7** para documentação atual de libs (FastAPI, psycopg 3, redis-py, PyVis) antes de implementar.
