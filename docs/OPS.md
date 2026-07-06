# OPS — svc-kg (Knowledge Graph)

Guia prático de operações e deploy do micro-serviço **svc-kg** (FastAPI + Python 3.11).
O serviço lê os dados diretamente do **Postgres do stack mondaha** (`DATABASE_URL`),
opcionalmente usa **Redis** para cache e renderiza visualizações do grafo (vis-network / PyVis).

> **Migração concluída:** Supabase foi **deprecado**. O alvo atual de produção é o
> **Postgres direto do mondaha**. As variáveis `SUPABASE_*` não são mais usadas.

---

## 1. Variáveis de ambiente

Base: `.env.exemple` (copie para `.env`). Valores lidos em `app.py` / `db_pg.py`.

### App

| Variável    | Default              | Descrição                                                   |
| ----------- | -------------------- | ----------------------------------------------------------- |
| `APP_ENV`   | `production`         | Ambiente lógico (`production` / `development`).             |
| `APP_HOST`  | `svc-kg.mondaha.com` | Hostname público do serviço (referência/deploy).            |
| `PORT`      | `8080`               | Porta HTTP que o servidor escuta.                           |
| `LOG_LEVEL` | `info`               | Nível de log (`debug`/`info`/`warning`/`error`).            |

### CORS

| Variável                 | Default                       | Descrição                                                    |
| ------------------------ | ----------------------------- | ----------------------------------------------------------- |
| `CORS_ALLOW_ORIGINS`     | `*`                           | Origens permitidas. `*` libera todas; senão lista por `,`.  |
| `CORS_ALLOW_METHODS`     | `GET,POST,OPTIONS`            | Métodos HTTP permitidos.                                    |
| `CORS_ALLOW_HEADERS`     | `Authorization,Content-Type`  | Headers permitidos.                                         |
| `CORS_ALLOW_CREDENTIALS` | `false`                       | Envia credenciais (cookies/auth). `true`/`false`.           |

### Cache (Redis)

| Variável              | Default                  | Descrição                                                         |
| --------------------- | ------------------------ | ---------------------------------------------------------------- |
| `ENABLE_REDIS_CACHE`  | `true`                   | Liga/desliga o cache Redis (fail-soft se indisponível).          |
| `REDIS_URL`           | `redis://redis:6379/0`   | DSN do Redis. Fora do docker use `redis://localhost:6379/0`.     |
| `CACHE_API_TTL`       | `60`                     | TTL (segundos) das respostas de grafo/HTML em cache.             |
| `CACHE_STATIC_MAX_AGE`| `86400`                  | `max-age` (segundos) do cache HTTP de assets estáticos.          |

### Postgres (stack mondaha)

| Variável          | Default                                                | Descrição                                                                 |
| ----------------- | ----------------------------------------------------- | ------------------------------------------------------------------------- |
| `DATABASE_URL`    | `postgresql://mondaha:mondaha@postgres:5432/mondaha`  | DSN do Postgres do mondaha. In-network use host `postgres`; via host `localhost`. |
| `PG_POOL_MAX`     | `10`                                                  | Tamanho máximo do pool de conexões (`psycopg_pool`).                      |
| `PG_POOL_TIMEOUT` | `10`                                                  | Timeout (segundos) para obter conexão do pool.                           |
| `KG_AUTO_MIGRATE` | `true`                                                | Se `true`, aplica `db/mondaha_install.sql` (idempotente) no startup.     |

### Tabela de fotos (opcional)

| Variável            | Default     | Descrição                                   |
| ------------------- | ----------- | ------------------------------------------- |
| `MEMBERS_TABLE`     | `membros`   | Tabela-fonte dos membros.                   |
| `MEMBERS_ID_COL`    | `id`        | Coluna de ID.                               |
| `MEMBERS_PHOTO_COL` | `photo_url` | Coluna com a URL da foto.                   |

### DEPRECATED — não usadas (migrado para Postgres direto)

| Variável              | Status       | Observação                                     |
| --------------------- | ------------ | ---------------------------------------------- |
| `SUPABASE_URL`        | DEPRECATED   | Substituída por `DATABASE_URL`.                |
| `SUPABASE_ANON_KEY`   | DEPRECATED   | Sem efeito.                                    |
| `SUPABASE_SERVICE_KEY`| DEPRECATED   | Sem efeito.                                    |
| `SUPABASE_RPC_FN`     | DEPRECATED   | Antigo RPC `get_graph_membros`.                |
| `SUPABASE_TIMEOUT`    | DEPRECATED   | Sem efeito.                                    |

> `SUPABASE_*` só sobrevivem no legado `docker-compose.local.yaml` (stack que emula Supabase via PostgREST).

---

## 2. Rodar local (sem Docker)

Pré-requisitos: **Postgres do mondaha** e **Redis** acessíveis (ex.: `localhost:5432` / `localhost:6379`).

```bash
# 1) dependências
pip install -r requirements.txt

# 2) variáveis (aponte para o host, não para os nomes de serviço docker)
export DATABASE_URL="postgresql://mondaha:mondaha@localhost:5432/mondaha"
export REDIS_URL="redis://localhost:6379/0"
export ENABLE_REDIS_CACHE=true
export KG_AUTO_MIGRATE=true

# 3a) via script (gunicorn + uvicorn worker, porta 8080)
bash start.sh

# 3b) ou dev com autoreload
uvicorn app:app --reload --port 8080
```

Serviço em `http://localhost:8080`. Se `KG_AUTO_MIGRATE=true`, a função SQL é instalada no startup.

---

## 3. Rodar com Docker contra o stack mondaha (recomendado)

Este é o modo alvo: o svc-kg **não sobe DB próprio** — reutiliza o `postgres` e o `redis`
do stack mondaha **já em execução**.

```bash
# 0) garanta que o stack mondaha (postgres + redis) está no ar
# 1) prepare o .env
cp .env.exemple .env

# 2) suba apenas o svc-kg conectado à rede do mondaha
docker compose -f docker-compose.mondaha.yml up --build
```

Serviço exposto em `http://localhost:8080`.

### Rede externa `mondaha_default`

O compose conecta o svc-kg à rede **externa** do docker-compose do mondaha. O nome padrão é
`<nome-do-projeto>_default`, ou seja **`mondaha_default`**. Dentro dessa rede o svc-kg alcança
o Postgres pelo host `postgres` e o Redis pelo host `redis`.

Se o stack mondaha subiu com `-p <projeto>` (ex.: `docker compose -p meuproj up`), a rede será
`<projeto>_default`. Ajuste em `docker-compose.mondaha.yml`:

```yaml
networks:
  mondaha:
    external: true
    name: mondaha_default   # <-- troque pelo nome real
```

Confirme o nome real com:

```bash
docker network ls | grep default
```

---

## 4. Outros arquivos compose (quando usar cada um)

| Arquivo                        | Uso                                                                                                   | DB / Cache                                        |
| ------------------------------ | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| `docker-compose.mondaha.yml`   | **Recomendado.** Só svc-kg, conecta ao stack mondaha em execução (rede externa `mondaha_default`).    | Postgres + Redis do mondaha (in-network).         |
| `docker-compose.yaml`          | Compose padrão (Coolify/prod): svc-kg + **Redis próprio**; `DATABASE_URL` vem do `.env`/SSM. Rede externa `obs_net`. | Postgres externo (via `DATABASE_URL`) + Redis local. |
| `docker-compose.local.yaml`    | **Legado / dev isolado.** Stack local completo que **emula Supabase via PostgREST** (postgres + postgrest + redis + svc-kg). Usa `.env.local` e `SUPABASE_*`. | Postgres local + PostgREST (emulação).            |
| `docker-compose.coolify.yml`   | Deploy no **Coolify**: svc-kg + Redis; ainda referencia `SUPABASE_*` (perfil legado de deploy).       | Redis local; backend via env do Coolify.          |

> **Alvo atual de produção:** Postgres direto do mondaha (`docker-compose.mondaha.yml` ou
> `docker-compose.yaml` com `DATABASE_URL`). O `local.yaml`/`coolify.yml` com `SUPABASE_*` são legado.

---

## 5. Healthchecks

Três endpoints operacionais (tag `ops`):

| Endpoint         | Testa                                   | 200                         | 503                              |
| ---------------- | --------------------------------------- | --------------------------- | -------------------------------- |
| `GET /live`      | Liveness (processo no ar).              | Sempre `ok` (texto).        | —                                |
| `GET /health`    | Status geral (Redis + backend config).  | Serviço saudável.           | Redis/Postgres com falha.        |
| `GET /health?deep=true` | Idem + **ping real ao Postgres**. | Postgres respondeu.         | Postgres inacessível.            |
| `GET /ready`     | Readiness — **ping ao Postgres** (+ Redis). | Pronto para tráfego.    | Postgres inacessível / não pronto. |

```bash
curl -s http://localhost:8080/live            # -> ok
curl -s http://localhost:8080/health | jq
curl -s "http://localhost:8080/health?deep=true" | jq
curl -s http://localhost:8080/ready | jq
```

- **200** = saudável/pronto.
- **503** = dependência crítica falhou (Redis quando `ENABLE_REDIS_CACHE=true`, ou Postgres em `/ready` e `/health?deep=true`).

### Healthcheck do Docker

- `docker-compose.mondaha.yml` e `docker-compose.local.yaml` usam `/health`.
- `docker-compose.yaml` e `docker-compose.coolify.yml` usam `/live` (liveness puro).
- Config padrão: `interval 10s`, `timeout 5s`, `retries 5`.

---

## 6. KG_AUTO_MIGRATE

No startup, se `KG_AUTO_MIGRATE=true`, o serviço chama `db_pg.ensure_schema()`, que aplica
**`db/mondaha_install.sql`** (idempotente: `CREATE OR REPLACE FUNCTION` + `CREATE INDEX IF NOT EXISTS`).
Falha na migração é **fail-soft** (loga `warning`, o serviço continua subindo).

Instalar / reaplicar manualmente:

```bash
psql "$DATABASE_URL" -f db/mondaha_install.sql
```

Rode manualmente quando `KG_AUTO_MIGRATE=false`, quando o usuário do app não tiver permissão de DDL,
ou para provisionar o schema antes do primeiro deploy.

---

## 7. Cache Redis

- **Habilitar/desabilitar:** `ENABLE_REDIS_CACHE=true|false`. Desligado, o serviço computa sempre
  sem cache. É **fail-soft**: se o Redis cair, as requisições seguem funcionando (só perdem cache).
- **TTL:** `CACHE_API_TTL` (segundos, default `60`) aplica-se às respostas de grafo e ao HTML renderizado.
- **Chaves usadas:**
  - `kg:graph:<faccao_id>:<include_co>:<max_pairs>` — respostas JSON do grafo.
  - `kg:html:<visjs|pyvis>:...` — HTML das visualizações.

Invalidar (flush) o cache via `redis-cli`:

```bash
# apagar todas as chaves de grafo
redis-cli --scan --pattern 'kg:graph:*' | xargs -r redis-cli del

# apagar o HTML renderizado
redis-cli --scan --pattern 'kg:html:*'  | xargs -r redis-cli del

# dentro do container do compose:
docker compose exec redis sh -c "redis-cli --scan --pattern 'kg:graph:*' | xargs -r redis-cli del"
```

> Preferir `--scan` a `KEYS` para não bloquear o Redis em produção.

---

## 8. Troubleshooting

| Sintoma                                   | Causa provável                                                        | Ação                                                                                             |
| ----------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `/ready` retorna **503**                  | Postgres inacessível.                                                 | Confira `DATABASE_URL`, se o `postgres` do mondaha está no ar e se a rede docker está correta.    |
| `/health?deep=true` **503**               | Ping ao Postgres falhou.                                              | Idem acima; veja `backend_error` no JSON de resposta.                                             |
| **Grafo vazio** (`nodes`/`edges` = 0)     | Tabelas do mondaha sem dados **ou** função SQL não instalada.         | Verifique dados; confira `KG_AUTO_MIGRATE=true` e logs de `ensure_schema`; ou rode o `.sql` manual. |
| Erro de conexão / DNS (`postgres`/`redis` não resolve) | Rede docker errada — svc-kg não está na rede do mondaha.  | Ajuste `networks.mondaha.name` para a rede real (`docker network ls`), normalmente `mondaha_default`. |
| Redis com erro mas app funciona           | Cache fail-soft (comportamento esperado).                            | Verifique `REDIS_URL`/`ENABLE_REDIS_CACHE`; não bloqueia o serviço.                              |

Logs úteis no startup: linha `ensure_schema: schema aplicado (...)` (migração OK) ou
`ensure_schema falhou (...)` (DDL/permissão/conexão).

---

## 9. Smoke test

Script pronto (`test_svc_kg.sh`) — testa `/health`, `/openapi.json`, `/v1/graph/membros` e neighbors:

```bash
BASE_URL=http://localhost:8080 ./test_svc_kg.sh
```

Verificação manual mínima:

```bash
# readiness (deve responder 200 com Postgres no ar)
curl -s http://localhost:8080/ready | jq

# preview do grafo de membros
curl -s "http://localhost:8080/v1/graph/membros?max_nodes=50" | jq '{nodes: (.nodes|length), edges: (.edges|length)}'
```

Esperado: `/ready` com `"ok": true` e o grafo retornando contagens de `nodes`/`edges` > 0
(quando houver dados nas tabelas do mondaha).
