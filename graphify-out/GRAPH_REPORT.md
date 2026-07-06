# Graph Report - svc-kg  (2026-07-06)

## Corpus Check
- 21 files · ~24,897 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 170 nodes · 197 edges · 14 communities (12 shown, 2 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `5cde76d7`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]

## God Nodes (most connected - your core abstractions)
1. `CHANGELOG` - 10 edges
2. `CLAUDE.md — svc-kg` - 10 edges
3. `Arquitetura — svc-kg (Knowledge Graph)` - 10 edges
4. `OPS — svc-kg (Knowledge Graph)` - 10 edges
5. `svc-kg — Referência HTTP da API` - 9 edges
6. `_get_redis()` - 8 edges
7. `Camada de dados — svc-kg` - 8 edges
8. `fetch_graph_sanitized()` - 7 edges
9. `vis_visjs()` - 7 edges
10. `1. Variáveis de ambiente` - 7 edges

## Surprising Connections (you probably didn't know these)
- None detected - all connections are within the same source files.

## Import Cycles
- None detected.

## Communities (14 total, 2 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.16
Nodes (23): Any, _env_backend_ok(), fetch_graph_sanitized(), _get_redis(), graph_membros(), health(), _html_cache_get(), _html_cache_set() (+15 more)

### Community 1 - "Community 1"
Cohesion: 0.07
Nodes (27): Adicionado, Adicionado, Adicionado, Adicionado, Alterado, Alterado, Alterado, Alterado (+19 more)

### Community 2 - "Community 2"
Cohesion: 0.11
Nodes (18): Autenticação, Base URL, Documentação e estáticos, Embed no app mondaha, `GET /docs` — Swagger UI custom, `GET /health` — Health check, `GET /live` — Liveness, `GET /ops/status` — Status operacional (+10 more)

### Community 3 - "Community 3"
Cohesion: 0.11
Nodes (18): 1. Variáveis de ambiente, 2. Rodar local (sem Docker), 3. Rodar com Docker contra o stack mondaha (recomendado), 4. Outros arquivos compose (quando usar cada um), 5. Healthchecks, 6. KG_AUTO_MIGRATE, 7. Cache Redis, 8. Troubleshooting (+10 more)

### Community 4 - "Community 4"
Cohesion: 0.15
Nodes (16): AsyncConnectionPool, backend_ok(), close_pool(), ensure_schema(), fetch_graph(), _get_pool(), _mask_dsn(), pg_ping() (+8 more)

### Community 5 - "Community 5"
Cohesion: 0.12
Nodes (15): 1. Visão, 2. Tabelas lidas (no banco `mondaha`), 3. Função `public.get_graph_membros`, 4. Instalação idempotente, 5. Interface `db_pg.py`, 6. Diferenças vs Supabase (migração), 7. Paridade de schema, Arquivos legados (referência/emulação local — não usados contra o mondaha) (+7 more)

### Community 6 - "Community 6"
Cohesion: 0.18
Nodes (10): 1. Visão geral, 2. Stack, 3. Arquitetura (1 parágrafo), 4. Gerado vs editável / cuidado, 5. Comandos, 6. Variáveis de ambiente principais, 7. Rotas, 8. Convenções (+2 more)

### Community 7 - "Community 7"
Cohesion: 0.18
Nodes (10): 1. Contexto, 2. Diagrama de componentes, 3. Fluxo de request de dados, 4. Construção do grafo, 5. Camadas de render, 6. Cache (Redis), 7. Camada de dados (`db_pg.py`), 8. Decisões / histórico (+2 more)

### Community 8 - "Community 8"
Cohesion: 0.29
Nodes (6): 1. Crie `.env` a partir de `.env.example` e defina:, Endpoints, Env vars principais, Rodando contra o Postgres do mondaha, Rodando LOCAL (Postgres + Redis), svc-kg (v1.8.0)

### Community 10 - "Community 10"
Cohesion: 0.83
Nodes (3): fail(), pass(), test_svc_kg.sh script

## Knowledge Gaps
- **83 isolated node(s):** `Response`, `AsyncConnectionPool`, `graph.sh script`, `start.sh script`, `PYTHONPATH` (+78 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What connects `Response`, `Mascara a DATABASE_URL para status/logs: esconde user:senha, mostra só host/db.`, `GET fail-soft do cache de HTML no Redis.` to the rest of the system?**
  _94 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.07142857142857142 - nodes in this community are weakly interconnected._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.10526315789473684 - nodes in this community are weakly interconnected._
- **Should `Community 3` be split into smaller, more focused modules?**
  _Cohesion score 0.10526315789473684 - nodes in this community are weakly interconnected._
- **Should `Community 4` be split into smaller, more focused modules?**
  _Cohesion score 0.14705882352941177 - nodes in this community are weakly interconnected._
- **Should `Community 5` be split into smaller, more focused modules?**
  _Cohesion score 0.125 - nodes in this community are weakly interconnected._