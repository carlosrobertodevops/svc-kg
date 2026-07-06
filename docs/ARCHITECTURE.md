# Arquitetura — svc-kg (Knowledge Graph)

Documento técnico de arquitetura do microserviço **svc-kg** (FastAPI). Descreve
o papel do serviço no ecossistema mondaha, o fluxo de dados, a construção do
grafo, as camadas de renderização, o cache Redis e a camada de acesso a dados.

> Base factual: `app.py` (v1.7.20), `db_pg.py`, `db/mondaha_install.sql`,
> `utils/get_membros_graph.sql`, `README.md`.

---

## 1. Contexto

O **svc-kg** é um microserviço isolado (repositório separado do monorepo
mondaha) responsável por servir o **grafo de conhecimento** que relaciona
**membros ↔ facções ↔ funções** do domínio policial/investigativo.

No produto mondaha, a tela **"Conhecimento"** não usa a stack Sigma.js do
front Next.js: ela embute o svc-kg via **iframe**, apontando para a rota de
visualização `GET /v1/vis/pyvis` (HTML PyVis pronto para renderizar). O front
apenas monta a URL com os parâmetros de filtro (ex.: `faccao_id`, `theme`,
`title`) e delega toda a montagem/renderização do grafo ao serviço.

Responsabilidades do svc-kg:

- Ler o grafo direto do **Postgres do mondaha** (função SQL `get_graph_membros`).
- Sanitizar/normalizar os dados (labels, arrays PG, arestas órfãs).
- Cachear o resultado no **Redis** (grafo e HTML renderizado).
- Renderizar visualizações HTML autocontidas (**PyVis** e **vis.js**) e expor
  o grafo em **JSON** puro.
- Prover sondas de saúde/operacionais (`/live`, `/health`, `/ready`, `/ops/status`).

---

## 2. Diagrama de componentes

```
                          Ecossistema mondaha
 ┌───────────────────────────────────────────────────────────────────────┐
 │                                                                         │
 │   ┌──────────────────────────┐                                         │
 │   │  App mondaha (Next.js)    │                                         │
 │   │  tela "Conhecimento"      │                                         │
 │   │  <iframe src=             │                                         │
 │   │   .../v1/vis/pyvis?...>    │                                         │
 │   └───────────┬──────────────┘                                         │
 │               │ HTTP (GET, filtros na query string)                    │
 │               ▼                                                         │
 │   ┌──────────────────────────────────────────────────────────┐        │
 │   │                svc-kg — FastAPI (app.py)                   │        │
 │   │                                                            │        │
 │   │  Rotas de dados/viz:                                       │        │
 │   │   • /v1/graph/membros   (JSON)                             │        │
 │   │   • /v1/vis/visjs       (HTML vis-network)                 │        │
 │   │   • /v1/vis/pyvis       (HTML PyVis + toolbar)             │        │
 │   │  Ops: /live /health /ready /ops/status /docs               │        │
 │   │                                                            │        │
 │   │  Pipeline: fetch_graph_sanitized →                         │        │
 │   │   normalize_graph_labels → truncate_preview                │        │
 │   └───────┬───────────────────────────────────┬──────────────┘        │
 │           │                                    │                        │
 │           │ db_pg.fetch_graph()                │ cache GET/SET          │
 │           │ (psycopg3 async pool)              │ (fail-soft)            │
 │           ▼                                    ▼                        │
 │   ┌──────────────────────────┐      ┌────────────────────────┐         │
 │   │  Postgres mondaha         │      │  Redis (lateral)        │         │
 │   │  public.get_graph_membros │      │  kg:graph:*  (grafo)    │         │
 │   │  (SQL, jsonb nodes/edges) │      │  kg:html:*   (HTML)     │         │
 │   │  membros / faccoes /       │      └────────────────────────┘         │
 │   │  funcoes                   │                                        │
 │   └──────────────────────────┘                                        │
 │                                                                         │
 └───────────────────────────────────────────────────────────────────────┘

 Render (dentro do FastAPI):  PyVis (pyvis.network.Network)  |  vis.js (vis-network)
```

Fluxo resumido: **Cliente (iframe) → FastAPI (`app.py`) → camada de dados
(`db_pg.py`, pool psycopg3) → Postgres mondaha (`get_graph_membros`)**, com
**Redis** como cache lateral e **PyVis / vis.js** como camadas de render.

---

## 3. Fluxo de request de dados

Toda rota que precisa do grafo converge para `fetch_graph_sanitized(faccao_id,
include_co, max_pairs, use_cache)` em `app.py`:

1. **Rota** (`/v1/graph/membros`, `/v1/vis/pyvis`, `/v1/vis/visjs`) recebe os
   query params e chama `fetch_graph_sanitized(...)`.
2. **Cache GET (grafo)** — monta a chave `kg:graph:{faccao_id}:{include_co}:{max_pairs}`
   e tenta `redis.get`. Hit → desserializa o JSON e retorna.
3. **`db_pg.fetch_graph(faccao_id, include_co, max_pairs)`** — executa
   `SELECT public.get_graph_membros(%s, %s, %s)` e obtém o `jsonb`
   `{nodes, edges}` (normaliza `None`/tipos inesperados para `{nodes:[], edges:[]}`).
4. **`normalize_graph_labels(data)`** — sanitização:
   - Converte `id` de todos os nós para **string**.
   - Limpa labels no formato de **array textual do Postgres** (`{a,b,null}` →
     `a, b`) via `_normalize_pg_text_array_label`, removendo `null`/vazios.
   - **Descarta arestas órfãs**: só mantém arestas cujos `source` e `target`
     existem no conjunto de `id`s dos nós.
5. **Cache SET (grafo)** — grava o resultado normalizado na chave `kg:graph:*`
   com TTL `CACHE_API_TTL`.
6. **`truncate_preview(data, max_nodes, max_edges)`** — recorta o grafo para o
   preview: mantém os primeiros `max_nodes` nós e apenas as arestas cujas pontas
   sobrevivem ao corte (depois limita a `max_edges`).

> **Importante — `max_nodes`/`max_edges` são PÓS-cache.** A chave de cache do
> grafo (`kg:graph:{faccao_id}:{include_co}:{max_pairs}`) **não** inclui
> `max_nodes`/`max_edges`. O grafo é cacheado inteiro (sanitizado) e o
> truncamento é aplicado **depois** da leitura do cache, na própria rota. Assim,
> variar apenas `max_nodes`/`max_edges` reaproveita o mesmo grafo cacheado; só
> `faccao_id`, `include_co` e `max_pairs` produzem novas entradas de cache de grafo.

---

## 4. Construção do grafo

A lógica de **nós e arestas vive na função SQL** `public.get_graph_membros(bigint,
boolean, integer)` — **não** no Python. O svc-kg apenas transporta, sanitiza e
renderiza o `jsonb` retornado. A função é `language sql`, `stable`,
`security invoker`.

**Arestas diretas:**

| Relação            | De → Para        | Peso | Origem                       |
| ------------------ | ---------------- | ---- | ---------------------------- |
| `PERTENCE_A`       | membro → facção  | 3.0  | `membros.faccao_id`          |
| `EXERCE`           | membro → função  | 3.0  | `membros.funcao_id`          |
| `FUNCAO_DA_FACCAO` | função → facção  | 2.0  | `funcoes.faccao_id`          |

**Co-ocorrências (opcionais, só quando `p_include_co = true`):**

| Relação      | Regra                                                  | Peso | Limite       |
| ------------ | ------------------------------------------------------ | ---- | ------------ |
| `CO_FACCAO`  | pares de membros na **mesma facção** (`m1<m2`)         | 0.5  | `p_max_pairs`|
| `CO_FUNCAO`  | pares de membros na **mesma função** (`m1<m2`)         | 0.8  | `p_max_pairs`|

**Nós** (`membro`, `faccao`, `funcao`):

- **Label anonimizado**: o nó de membro **nunca** expõe `membro_id` no label.
  Usa o 1º valor não-vazio de `alcunha` (que no mondaha é `varchar[]`, extraído
  via `array_to_string`) com fallback para `nome_completo` e, por fim,
  `'Sem nome'`. Facções/funções caem em `nome` com fallback `'Facção <id>'` /
  `'Função <id>'`.
- **`group`** = `faccao_id` (`coalesce(..., 0)`) — usado pela render para colorir
  por facção.
- **`size`** = função do **grau** do nó: `greatest(10, 10 + ln(degree+1) * 8)`.
- **`type`** = `membro` | `faccao` | `funcao`.

**Retorno** (`jsonb` único):

```json
{
  "nodes": [{ "id": "...", "label": "...", "type": "...", "group": 0, "size": 10 }],
  "edges": [{ "source": "...", "target": "...", "weight": 3.0, "relation": "PERTENCE_A" }]
}
```

O parâmetro `p_faccao_id` filtra o subgrafo por facção (`nullif(p_faccao_id, 0)`
→ `null` significa "todas"). Índices de suporte instalados junto:
`ix_membros_faccao_membro`, `ix_membros_funcao_membro`, `ix_funcoes_faccao`.

---

## 5. Camadas de render

Três rotas consomem o mesmo pipeline de dados e diferem no formato de saída:

- **`GET /v1/graph/membros`** — JSON puro `{nodes, edges}`. Aplica
  `fetch_graph_sanitized` + `truncate_preview`. É o endpoint que o modo
  **client** do vis.js consulta via `fetch`.

- **`GET /v1/vis/visjs`** — HTML com **vis-network**. Dois modos via
  `source=server|client`:
  - **server** (default): o grafo é embutido no HTML como
    `<script id="__KG_DATA__" type="application/json">…</script>` e renderizado
    no browser sem chamada extra.
  - **client**: o HTML busca `/v1/graph/membros` via `fetch` no browser.
  - Assets vis-network servidos **localmente** (`/static/vendor/...`) se
    presentes, senão via `unpkg`. Paleta por facção (CV vermelho, PCC azul
    escuro, funções amarelo), arestas ultrafinas, física desligada após
    estabilizar, busca/destaque e cor de aresta inferida por CV/PCC.

- **`GET /v1/vis/pyvis`** — HTML gerado pelo **PyVis** (`pyvis.network.Network`,
  `cdn_resources="in_line"` → JS/CSS embutidos). É a rota consumida pelo iframe
  da tela "Conhecimento". Após `net.generate_html()`, o serviço **injeta uma
  toolbar** (CSS + HTML + JS) via `str.replace` em `</head>`, `<body>` e
  `</body>`, adicionando busca, imprimir e recarregar. Cores fixas por facção
  (PCC azul, CV vermelho), funções amarelas e `hash_color` (HSL) para as demais.

**CSP e headers**: as respostas HTML do vis.js retornam `Content-Security-Policy`
(`_VISJS_CSP` — permite `img-src` http/https para `photo_url` e `unpkg` para
script/style) e `X-Content-Type-Options: nosniff`. A rota `/docs` (Swagger UI
custom com barra de ops) usa CSP própria liberando `cdn.jsdelivr.net`. CORS é
configurável por env (`CORS_ALLOW_ORIGINS`, default `*`).

---

## 6. Cache (Redis)

Cache **lateral**, opt-in e **fail-soft** (qualquer erro no Redis é logado como
warning e **não** quebra a rota — o serviço recai na leitura direta do Postgres).

Habilitação: `ENABLE_REDIS_CACHE=true` **e** o parâmetro `cache=true` na request.
Cliente `redis.asyncio` singleton, criado sob demanda.

Dois níveis de cache:

1. **Grafo** — chave `kg:graph:{faccao_id}:{include_co}:{max_pairs}`; valor é o
   `{nodes, edges}` **normalizado** (JSON). Gerido em `fetch_graph_sanitized`.
   Não inclui `max_nodes`/`max_edges` (ver seção 3).
2. **HTML renderizado** — chave `kg:html:{rota}:{todos os params}` incluindo
   `faccao_id, include_co, max_pairs, max_nodes, max_edges, theme, title` (+
   `source`/`debug` no visjs). Gerido por `_html_cache_get`/`_html_cache_set`.

TTL de ambos = `CACHE_API_TTL` (default 60s). No shutdown a conexão Redis é
fechada junto ao pool do Postgres.

---

## 7. Camada de dados (`db_pg.py`)

Acesso **direto ao Postgres** do mondaha via **psycopg 3 async**, com pool
singleton lazy.

- **Pool**: `AsyncConnectionPool(conninfo=DATABASE_URL, min_size=1,
  max_size=PG_POOL_MAX(=10), timeout=PG_POOL_TIMEOUT(=10), open=False)` — aberto
  na primeira chamada que precisa de conexão.
- **Interface pública congelada**:
  - `fetch_graph(faccao_id, include_co, max_pairs) -> dict` — executa
    `SELECT public.get_graph_membros(%s,%s,%s)`; trata `jsonb` como `dict`
    (psycopg3) ou faz `json.loads` de robustez; sempre devolve
    `{nodes: [...], edges: [...]}`.
  - `pg_ping() -> bool` — `SELECT 1` (usado em `/health?deep=true` e `/ready`).
  - `ensure_schema() -> None` — instala/atualiza função + índices.
  - `backend_ok() -> bool` — `True` se `DATABASE_URL` está definida.
  - `close_pool() -> None` — fecha o pool no shutdown.
- **`ensure_schema()` no startup**: se `KG_AUTO_MIGRATE=true` (default), o evento
  de startup roda `db_pg.ensure_schema()`, que lê e executa
  `db/mondaha_install.sql` — script **idempotente** (`CREATE OR REPLACE FUNCTION`
  + `CREATE INDEX IF NOT EXISTS` + `ANALYZE`). Fail-soft: erro é logado e **não**
  derruba o serviço. O script **não** cria/altera tabelas — assume que
  `membros`, `faccoes`, `funcoes` já existem no mondaha.
- **Segurança em logs**: `_mask_dsn` mascara a senha da `DATABASE_URL`.

---

## 8. Decisões / histórico

- **Migração Supabase → Postgres direto.** A leitura antes era feita via
  **PostgREST** (HTTP, `httpx`) contra o Supabase; passou a ser **psycopg3**
  direto no Postgres do mondaha. **Só o transporte mudou** — a função SQL
  `get_graph_membros` foi reaproveitada praticamente inalterada.
- **Contrato da função congelado.** A assinatura canônica é `bigint` (a
  sobrecarga `integer` é dropada no install para evitar ambiguidade — antigo
  fix PGRST203 do PostgREST).
- **Limpeza de artefatos Supabase.** No `mondaha_install.sql` foram removidos os
  `grant ... to anon/authenticated/service_role` (roles do Supabase) e o
  `notify pgrst, 'reload schema'` (schema cache do PostgREST); a função passou a
  `security invoker`. As variáveis `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` estão
  **deprecadas** e não são mais usadas.
- **`alcunha` como `varchar[]`.** No mondaha a coluna é array; o label extrai o
  1º valor não-vazio via `array_to_string`, com fallback para `nome_completo`.

---

## 9. Pontos de atenção

- **Monólito `app.py`.** Toda a API (rotas de dados, duas render engines HTML com
  JS embutido, ops e `/docs`) vive num único arquivo grande. Alterações devem
  **preservar o contrato das rotas** (`/v1/graph/membros`, `/v1/vis/pyvis`,
  `/v1/vis/visjs`) — o iframe da tela "Conhecimento" depende de `/v1/vis/pyvis`.
- **Anonimização de labels (segurança).** O grafo **nunca** deve expor
  `membro_id` no label — a regra está na função SQL e reforçada por
  `normalize_graph_labels`/`cleanLabel`. Qualquer mudança na montagem de nós
  precisa manter essa invariante (labels de facção/função podem vazar id no
  fallback textual, mas membro não).
- **`max_nodes`/`max_edges` fora da chave de cache de grafo.** Truncamento é
  pós-cache; ver seção 3 ao ajustar limites.
- **Fail-soft em DB e cache.** Erros de Redis nunca quebram a rota; `ensure_schema`
  nunca derruba o startup; `fetch_graph` sempre retorna um dict válido. Manter
  esse comportamento defensivo ao evoluir o serviço.

---

*Documento gerado a partir da leitura direta de `app.py`, `db_pg.py`,
`db/mondaha_install.sql`, `utils/get_membros_graph.sql` e `README.md`.*
