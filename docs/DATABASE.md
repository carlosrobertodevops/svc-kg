# Camada de dados — svc-kg

Documentação da camada de dados do microserviço **svc-kg** (Knowledge Graph)
após a migração **Supabase → Postgres direto**.

---

## 1. Visão

O svc-kg lê o grafo de membros/facções/funções **direto do Postgres** do stack
mondaha, via **psycopg 3** (async + connection pool). Não há mais Supabase nem
PostgREST no caminho — o serviço chama uma função SQL no banco e recebe um único
`jsonb` de volta.

Connection string (variável de ambiente):

```bash
DATABASE_URL=postgresql://mondaha:mondaha@postgres:5432/mondaha
```

Fluxo em uma linha:

```
svc-kg  →  psycopg3 pool  →  SELECT public.get_graph_membros(...)  →  jsonb {nodes, edges}
```

---

## 2. Tabelas lidas (no banco `mondaha`)

O svc-kg **não cria nem altera tabelas** — ele apenas lê as tabelas já existentes
do stack mondaha. Colunas efetivamente usadas:

| Tabela          | Colunas usadas                                                    |
| --------------- | ----------------------------------------------------------------- |
| `public.membros`  | `membro_id`, `nome_completo`, `alcunha` (`varchar[]`), `faccao_id`, `funcao_id` |
| `public.faccoes`  | `faccao_id`, `nome`                                               |
| `public.funcoes`  | `funcao_id`, `nome`, `faccao_id`                                  |

> `alcunha` é um **array** (`varchar[]`) no schema mondaha. O label do nó de membro
> extrai o 1º valor não-vazio desse array (ver seção 3).

---

## 3. Função `public.get_graph_membros`

### Assinatura

```sql
public.get_graph_membros(
  p_faccao_id  bigint  default null,
  p_include_co boolean default true,
  p_max_pairs  integer default 8000
) returns jsonb
language sql
stable
security invoker
```

`LANGUAGE sql`, `STABLE`, `SECURITY INVOKER` — roda com os privilégios do papel da
connection string (não há `SECURITY DEFINER` nem roles Supabase envolvidos).

### Retorno

Um único `jsonb` no formato:

```json
{
  "nodes": [ { "id", "label", "type", "group", "size" }, ... ],
  "edges": [ { "source", "target", "weight", "relation" }, ... ]
}
```

Ambas as listas usam `coalesce(jsonb_agg(...), '[]'::jsonb)` — nunca `null`.

### Lógica

**Parâmetro de facção** — `nullif(p_faccao_id, 0)` trata `0` como "sem filtro"
(equivalente a `null`); qualquer outro valor filtra por `faccao_id`.

**Arestas diretas** (`direct_edges`):

| Relação            | Origem → Destino    | Peso |
| ------------------ | ------------------- | ---- |
| `PERTENCE_A`       | membro → facção     | 3.0  |
| `EXERCE`           | membro → função     | 3.0  |
| `FUNCAO_DA_FACCAO` | função → facção     | 2.0  |

**Co-ocorrências** (opcionais, só incluídas quando `p_include_co = true`):

| Relação      | Origem → Destino  | Peso | Regra                                                     |
| ------------ | ----------------- | ---- | --------------------------------------------------------- |
| `CO_FACCAO`  | membro → membro   | 0.5  | self-join por mesma `faccao_id`, `m1.membro_id < m2.membro_id`, `LIMIT p_max_pairs` |
| `CO_FUNCAO`  | membro → membro   | 0.8  | self-join por mesma `funcao_id` (não-nula), `m1.membro_id < m2.membro_id`, `LIMIT p_max_pairs` |

A condição `m1.membro_id < m2.membro_id` evita pares duplicados e auto-arestas;
`LIMIT p_max_pairs` limita o custo do produto cartesiano das co-ocorrências.

**Nós** (`base_nodes` = membros + facções + funções):

- **Label do membro**: 1º valor não-vazio de `alcunha` (via
  `array_to_string(m.alcunha, ', ')` + `nullif(btrim(...), '')`), com fallback
  para `nome_completo` e, por fim, `'Sem nome'`. O `membro_id` **NUNCA** aparece
  no label.
- **Label de facção/função**: `nome`, com fallback `'Facção <id>'` / `'Função <id>'`.
- **`size`**: proporcional ao grau do nó — `greatest(10, 10 + ln(degree+1) * 8)`.
- **`group`**: `coalesce(faccao_id, 0)` (usado para colorir/agrupar por facção).
- **`type`**: `'membro'` | `'faccao'` | `'funcao'`.

O grau (`deg`) é calculado contando arestas onde o nó aparece como `source` ou `target`.

---

## 4. Instalação idempotente

O script `db/mondaha_install.sql` instala a função + índices no banco mondaha.
É **idempotente** (pode rodar N vezes sem erro):

- `CREATE OR REPLACE FUNCTION` para a função canônica;
- `CREATE INDEX IF NOT EXISTS` para os índices;
- `DROP FUNCTION IF EXISTS ...(integer, boolean, integer)` remove a sobrecarga
  antiga em `integer`, mantendo só a assinatura `bigint`;
- `ANALYZE` em `membros`, `funcoes`, `faccoes` ao final.

### Índices de suporte

```sql
create index if not exists ix_membros_faccao_membro on public.membros (faccao_id, membro_id);
create index if not exists ix_membros_funcao_membro on public.membros (funcao_id, membro_id);
create index if not exists ix_funcoes_faccao        on public.funcoes (faccao_id);
```

### Como aplicar

**Automático (startup)** — se `KG_AUTO_MIGRATE=true`, o serviço chama
`db_pg.ensure_schema()` no boot, que executa o `db/mondaha_install.sql`.

**Manual**:

```bash
psql "$DATABASE_URL" -f db/mondaha_install.sql
```

---

## 5. Interface `db_pg.py`

Módulo de acesso ao Postgres via psycopg 3, com pool assíncrono singleton
(`AsyncConnectionPool`, criado lazy na 1ª conexão). Interface pública congelada:

| Função                                            | Retorno | Descrição                                                                 |
| ------------------------------------------------- | ------- | ------------------------------------------------------------------------- |
| `fetch_graph(faccao_id, include_co, max_pairs)`   | `dict`  | Executa `get_graph_membros` e devolve `{'nodes': [...], 'edges': [...]}`; retorno vazio/None vira `{nodes:[], edges:[]}`. |
| `pg_ping()`                                        | `bool`  | `SELECT 1`; `True` se o banco respondeu, `False` em qualquer erro.        |
| `ensure_schema()`                                 | `None`  | Aplica `db/mondaha_install.sql` (idempotente). **Fail-soft**: loga erro e não propaga (não derruba o startup). |
| `backend_ok()`                                    | `bool`  | `True` se `DATABASE_URL` está configurada (não vazia).                    |
| `close_pool()`                                    | `None`  | Fecha o pool (shutdown).                                                  |

### Pool

`AsyncConnectionPool` com:

- `min_size=1`;
- `max_size` = env `PG_POOL_MAX` (default `10`);
- `timeout` = env `PG_POOL_TIMEOUT` (default `10` s);
- aberto explicitamente via `await pool.open()`.

A senha da `DATABASE_URL` é **mascarada em logs** (`_mask_dsn` → `user:***@host`).
`fetch_graph` ainda faz `json.loads` defensivo caso o `jsonb` volte como `str`.

---

## 6. Diferenças vs Supabase (migração)

Antes da migração, o grafo era obtido via **RPC PostgREST HTTP** contra o Supabase.
O que mudou:

- **Removidos os grants** a `anon`, `authenticated`, `service_role` (roles
  específicos do Supabase).
- **Removido** `notify pgrst, 'reload schema'` (recarga do schema cache do PostgREST —
  irrelevante em Postgres direto).
- **Removido** o fallback de parâmetros com prefixo `p_` (necessário só via PostgREST).
- Função agora é `SECURITY INVOKER` — o papel vem da **connection string**; RLS e
  roles do Supabase não se aplicam.

### Arquivos legados (referência/emulação local — não usados contra o mondaha)

| Arquivo                       | Papel                                                                                   |
| ----------------------------- | --------------------------------------------------------------------------------------- |
| `utils/get_membros_graph.sql` | Versão anterior da função (com `grant ... to anon/authenticated/service_role` + `notify pgrst`). Referência histórica. |
| `db/00_init.sql`              | Schema + seed de **exemplo** (tabelas mínimas com `alcunha text`, `SECURITY DEFINER`); para stack local isolado, não o mondaha. |
| `db/01_indexes.sql`           | Índices soltos (equivalentes aos do install), uso local.                                |
| `db/02_alias.sql`             | Alias `et_graph_membros` para compatibilidade com chamadas legadas; emulação local.     |

O único script canônico contra o banco mondaha é **`db/mondaha_install.sql`**.

---

## 7. Paridade de schema

Paridade confirmada com o schema **Drizzle** do mondaha:

- `public.membros` — `membro_id`, `nome_completo`, `alcunha` (**`varchar[]`**), `faccao_id`, `funcao_id`;
- `public.faccoes` — `faccao_id`, `nome`;
- `public.funcoes` — `funcao_id`, `nome`, `faccao_id`.

Ponto de atenção: `alcunha` é **`varchar[]`** no mondaha (não `text` como no
`db/00_init.sql` de exemplo, nem `jsonb`). Por isso o `mondaha_install.sql` usa
`array_to_string(m.alcunha, ', ')` para derivar o label, enquanto a versão legada
`utils/get_membros_graph.sql` fazia detecção via `jsonb_typeof(to_jsonb(...))`.
```