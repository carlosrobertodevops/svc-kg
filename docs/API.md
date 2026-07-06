# svc-kg — Referência HTTP da API

Referência completa das rotas do micro-serviço **svc-kg** (Knowledge Graph com visualizações vis.js e PyVis). Versão do serviço: `v1.7.20`. Extraído diretamente de `app.py`.

## Base URL

| Ambiente | URL |
| --- | --- |
| Local | `http://localhost:8080` |
| Produção | `https://svc-kg.mondaha.com` |

> Nos exemplos abaixo, `{KG}` representa a base URL escolhida.

## Autenticação

O serviço **não exige autenticação** (sem token, sem header de auth). O acesso é controlado por **CORS via variáveis de ambiente**:

- `CORS_ALLOW_ORIGINS` (default `*`) — lista separada por vírgula ou `*`.
- `CORS_ALLOW_CREDENTIALS` (default `false`).
- `CORS_ALLOW_METHODS` (default `GET,POST,OPTIONS`).
- `CORS_ALLOW_HEADERS` (default `Authorization,Content-Type`).

## Semântica de `cache`

O parâmetro `cache` (nas rotas de grafo/visualização) só tem efeito quando o cache Redis está habilitado via `ENABLE_REDIS_CACHE=true` (default `false`). Quando habilitado:

- `/v1/graph/membros` cacheia o JSON do grafo com chave `kg:graph:{faccao_id}:{include_co}:{max_pairs}` e TTL `CACHE_API_TTL` (default `60s`).
- `/v1/vis/visjs` e `/v1/vis/pyvis` cacheiam o HTML renderizado (chaves `kg:html:visjs:...` / `kg:html:pyvis:...`, mesmo TTL).

Com `ENABLE_REDIS_CACHE=false`, o parâmetro `cache` é aceito mas **não** produz cache (o backend Postgres é sempre consultado). O comportamento é *fail-soft*: falhas de Redis não derrubam a resposta.

---

## Rotas de operação (`ops`)

### `GET /live` — Liveness

Sonda de vida do processo. Não consulta backend.

- **Query params:** nenhum.
- **Resposta:** `text/plain`, corpo `ok`.
- **HTTP:** sempre `200`.

```bash
curl -i {KG}/live
```

---

### `GET /health` — Health check

Verifica Redis (ping, se habilitado) e o backend Postgres configurado. Com `deep=true`, executa também um probe ativo no Postgres (`db_pg.pg_ping`).

| Param | Tipo | Default | Observação |
| --- | --- | --- | --- |
| `deep` | boolean | `false` | Se `true`, faz probe real no Postgres (`pg_ping`). |

- **Resposta:** `application/json`. Inclui `platform_info` (hostname, container, `app_env`, `service_id`, `version`, etc.) mais:
  - `status`: `"ok"`
  - `redis`: boolean (ping do Redis; `false` se desabilitado)
  - `backend`: `"postgres"` quando configurado, senão `"none"`
  - `postgres`: `{ configured: bool, database_url: <DSN mascarada, sem user:senha> }`
  - `ok`: boolean agregado
  - opcionalmente `redis_error` / `backend_error`
- **HTTP:** `200` quando `ok=true`; `503` quando degradado (`ok=false`).

```bash
curl -i "{KG}/health"
curl -i "{KG}/health?deep=true"
```

---

### `GET /ready` — Readiness

Teste ativo de prontidão para tráfego. Faz ping no Redis (se habilitado) e probe Postgres (`db_pg.pg_ping`).

- **Query params:** nenhum.
- **Resposta:** `application/json` com `platform_info` + `ok` (boolean) e, opcionalmente, `redis` / `redis_error` / `backend_error`.
- **HTTP:** `200` se pronto (`ok=true`); `503` se não pronto.

```bash
curl -i {KG}/ready
```

---

### `GET /ops/status` — Status operacional

Retorna a configuração do ambiente e das dependências (sem expor segredos).

- **Query params:** nenhum.
- **Resposta:** `application/json` com `platform_info` +:
  - `redis`: `{ enabled: bool, url: <REDIS_URL>, ping?: bool, error?: string }`
  - `postgres`: `{ configured: bool, database_url: <DSN mascarada> }`
- **HTTP:** sempre `200`.

```bash
curl -s {KG}/ops/status | jq
```

---

## Rota de dados do grafo (`graph`)

### `GET /v1/graph/membros` — Grafo em JSON

Retorna o grafo bruto `{ nodes, edges }` a partir do Postgres (`db_pg.fetch_graph`), com normalização de labels e truncamento de preview.

| Param | Tipo | Default | Range/Limites |
| --- | --- | --- | --- |
| `faccao_id` | integer? | `null` | opcional (filtra por facção) |
| `include_co` | boolean | `true` | inclui arestas de co-relação (CO_FACCAO/CO_FUNCAO) |
| `max_pairs` | integer | `8000` | `1 .. 200000` |
| `max_nodes` | integer | `2000` | `50 .. 20000` |
| `max_edges` | integer | `4000` | `50 .. 200000` |
| `cache` | boolean | `true` | usa Redis quando `ENABLE_REDIS_CACHE=true` |

- **Resposta:** `application/json`:

```json
{
  "nodes": [ { "id": "...", "label": "...", "type": "...", "faccao_id": 0, "photo_url": "..." } ],
  "edges": [ { "source": "...", "target": "...", "relation": "...", "weight": 1 } ]
}
```

- **HTTP:** `200` OK. Valores fora do range dos params retornam `422` (validação FastAPI). Erro ao buscar grafo → `500` com `detail: "graph_fetch_error: ..."`.

```bash
curl -s "{KG}/v1/graph/membros?faccao_id=12&include_co=true&max_pairs=8000&max_nodes=2000&max_edges=4000&cache=true" | jq '.nodes | length'
```

---

## Rotas de visualização (`viz`)

### `GET /v1/vis/visjs` — Visualização vis-network (HTML)

Página HTML que carrega **vis-network** e renderiza o grafo. Cores por facção (CV vermelho, PCC azul, funções amarelas), arestas ultrafinas, física desligada após estabilizar, busca/destaque embutidos.

| Param | Tipo | Default | Range/Limites |
| --- | --- | --- | --- |
| `faccao_id` | integer? | `null` | opcional |
| `include_co` | boolean | `true` | — |
| `max_pairs` | integer | `8000` | sem `ge/le` (validação apenas de tipo) |
| `max_nodes` | integer | `2000` | sem `ge/le` |
| `max_edges` | integer | `4000` | sem `ge/le` |
| `cache` | boolean | `true` | usa Redis quando habilitado |
| `theme` | string | `light` | `light` \| `dark` (afeta background) |
| `title` | string | `Knowledge Graph (vis.js)` | título da página/toolbar |
| `debug` | boolean | `false` | atributo `data-debug` no container |
| `source` | string | `server` | `server` \| `client` (regex `^(server\|client)$`) — `server` embute os dados no HTML; `client` faz `fetch` de `/v1/graph/membros` |

- **Resposta:** `text/html` (200). Headers: `Content-Security-Policy` (permite imagens http/https para `photo_url` e scripts/styles de `unpkg.com`) e `X-Content-Type-Options: nosniff`.
- **HTTP:** `200` OK; `500` (`graph_fetch_error`) quando `source=server` e a busca do grafo falha. `source` fora do padrão → `422`.

```bash
curl -s "{KG}/v1/vis/visjs?faccao_id=12&theme=dark&source=server&title=KG%20Facção" -o visjs.html
```

---

### `GET /v1/vis/pyvis` — Visualização PyVis (HTML)

Página HTML gerada pelo **PyVis** (`pyvis.network.Network`, recursos CDN inline). Cores por facção, arestas ultrafinas, física desligada após estabilizar, toolbar minimalista com busca/imprimir/recarregar.

| Param | Tipo | Default | Range/Limites |
| --- | --- | --- | --- |
| `faccao_id` | integer? | `null` | opcional |
| `include_co` | boolean | `true` | — |
| `max_pairs` | integer | `8000` | sem `ge/le` |
| `max_nodes` | integer | `2000` | sem `ge/le` |
| `max_edges` | integer | `4000` | sem `ge/le` |
| `cache` | boolean | `true` | usa Redis quando habilitado |
| `theme` | string | `light` | `light` \| `dark` |
| `title` | string | `Knowledge Graph (PyVis)` | título da página/toolbar |

- **Resposta:** `text/html` (200). Sem grafo → `200` com `<h3>Sem dados para exibir.</h3>`.
- **HTTP:** `200` OK; `500` (`graph_fetch_error`) quando a busca do grafo falha.

```bash
curl -s "{KG}/v1/vis/pyvis?faccao_id=12&include_co=true&max_pairs=8000&max_nodes=2000&max_edges=4000&theme=dark" -o pyvis.html
```

---

## Documentação e estáticos

### `GET /docs` — Swagger UI custom

Página HTML de documentação (Swagger UI via `cdn.jsdelivr.net`) que consome `/openapi.json` (gerado pelo FastAPI) e exibe uma barra de ops (version, env, platform, host, redis, backend, postgres) alimentada por `/ops/status` e `/health?deep=true`.

- **Query params:** nenhum.
- **Resposta:** `text/html` (200) com CSP própria. `include_in_schema=False` (não aparece no OpenAPI).

```bash
open "{KG}/docs"
```

Rotas relacionadas geradas pelo FastAPI: `GET /openapi.json` (schema OpenAPI). O YAML de referência também é servido como estático em `/docs-static/openapi.yaml`.

### Mounts estáticos

| Mount | Diretório | Uso |
| --- | --- | --- |
| `/static` | `static/` | assets (ex.: `vis-network.min.js/.css` locais quando presentes, `vis-style.css`) |
| `/docs-static` | `docs/` | montado apenas se a pasta `docs` existir; expõe `openapi.yaml` |

---

## Embed no app mondaha

A tela de Conhecimento (Knowledge Graph) embeda a visualização PyVis via `<iframe>`:

```html
<iframe
  src="{KG}/v1/vis/pyvis?faccao_id=12&include_co=true&max_pairs=8000&max_nodes=2000&max_edges=4000&theme=dark"
  style="width:100%;height:90vh;border:0"
  loading="lazy"
></iframe>
```

Para permitir o embed, a CSP do app consumidor precisa liberar `frame-src` para a origem do svc-kg.

---

_Referência gerada a partir de `app.py` (v1.7.20) e alinhada com `docs/openapi.yaml`._
