
# CHANGELOG
Todas as mudanças notáveis deste projeto serão documentadas neste arquivo.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/)
e este projeto segue [SemVer](https://semver.org/lang/pt-BR/).

> Observação: as datas abaixo são aproximadas e foram consolidadas a partir do histórico recente do serviço em produção e dos ajustes reportados.

---

## [v1.7.20] - 2025-09-05
### Corrigido
- **/v1/vis/visjs** não renderizava e gerava `500` devido a `ValueError: Single '}' encountered in format string`.  
  **Correção**: página HTML agora é **estática** (sem `str.format`/f-string) e o JavaScript de montagem do grafo está em `static/vis-embed.js`.
- **/v1/vis/pyvis** retornava `500` com `pyvis error: Network.generate_html() got an unexpected keyword argument 'title'`.  
  **Correção**: uso de `generate_html()` **sem argumentos** e `set_options()` agora recebe **JSON válido** (antes era JS).
- Evitados `AssertionError: non existent node '0'` e erros similares em PyVis, garantindo que somente criamos arestas cujos nós existam.
- Melhor tratamento de labels vindos do Postgres em formato `{...}` (arrays textuais) e higienização de valores `"null"`.

### Alterado
- **/v1/vis/visjs**: continua com busca por nó/ID, **drag apenas do nó** (dragView desativado), **arestas finas** e destaque de nós encontrados.
- **Cores por facção**: nós associados a **CV** agora ficam **vermelhos** (`#d32f2f`) e nós associados a facções com **"PCC"** ficam **azul-escuro** (`#0d47a1`).
- **Fotos de pessoas**: quando há `photo_url` válida, o nó usa `circularImage`.
- **/docs** (Swagger UI custom): mantido o painel de status (links para `/live`, `/health`, `/health?deep=true`, `/ready`, `/ops/status` e **openapi.yaml**).

### Não alterado (confirmado)
- `Dockerfile`, `docker-compose.yaml`, `docker-compose.local.yaml` e `.env` preservados conforme estavam funcionando.

---

## [v1.7.19] - 2025-09-05 — _YANKED_ (não recomendada)
> Versão intermediária com tentativas de correção dos 500 nos endpoints de visualização.  
> Substituída integralmente pela **v1.7.20**.

### Alterado
- Início da remoção de f-strings/`.format()` nas páginas de visualização.
- Primeira tentativa de ajustar `set_options()` e `generate_html()` do PyVis.

### Conhecidos
- Persistiam erros `500` em **/v1/vis/visjs** e **/v1/vis/pyvis** (corrigidos de fato na **v1.7.20**).

---

## [v1.7.18] - 2025-09-05
### Corrigido
- **404 no Supabase RPC** (`get_graph_membros`) ao chamar com chaves erradas.  
  **Correção**: payload passa a usar **apenas** os parâmetros `p_faccao_id`, `p_include_co` e `p_max_pairs`, conforme a função RPC.

### Adicionado
- Robustez em sondas: `/health?deep=true` realiza RPC real no Supabase e `ping` no Redis.
- Tratamento de erros operacional propagado para `/health` e `/ready`.

### Alterado
- Normalização de labels (arrays do Postgres) e truncamento de visualização (`max_nodes`/`max_edges`) antes de retornar/plotar os dados.
- Configurações de CORS preservadas e documentadas.

---

## [v1.7.17] - 2025-09-05
### Adicionado
- **/docs** custom (Swagger UI) com **painel de status** no topo, exibindo:
  - Versão, ambiente, plataforma (Coolify/container), host;
  - Estado de Redis e Supabase (sem expor segredos).
- **/ops/status**: endpoint JSON com informações operacionais (versionamento, ambiente, Redis, Supabase com **redação** da chave).
- **/live**, **/health** e **/ready**: endpoints de liveness/health/readiness.

### Alterado
- Integração do `/docs` com `openapi.json` do serviço e link opcional para `docs-static/openapi.yaml`.

---

## [v1.7.13] - 2025-09-04
> Marco inicial conhecido desta linha 1.7.x no serviço “svc-kg”.

### Adicionado
- **/v1/graph/membros**: endpoint de dados (nós/arestas) com suporte a `faccao_id`, `include_co`, `max_pairs`, `max_nodes`, `max_edges` e `cache`.
- Visualizações:
  - **/v1/vis/visjs** (vis-network): toolbar com busca, impressão e reload; cores por facção; física ajustada; arestas finas.
  - **/v1/vis/pyvis** (PyVis): visual com física, cores por facção e espessura de arestas reduzida.
- **Cache Redis** (opcional): chaves `kg:graph:{hash}`, com TTL configurável via `CACHE_API_TTL`.
- Vendor do **vis-network** baixado localmente (sem CDN) em `static/vendor`.

### Alterado
- Normalização inicial de labels e coerção de tipos de ID para string no transporte.
- Estrutura base do projeto (FastAPI + Gunicorn + UvicornWorker) e estáticos servidos via `StaticFiles`.

---

## [≤ v1.7.12] - Histórico anterior
> Versões anteriores a 1.7.13 não estão documentadas neste repositório, mas incluíam a espinha dorsal do micro-serviço com FastAPI, integração com PostgREST/Supabase e primeiros protótipos de visualização.

---

## Notas de Compatibilidade / Migração

- **Supabase RPC**: a função `get_graph_membros` deve aceitar **apenas** os parâmetros `p_faccao_id`, `p_include_co`, `p_max_pairs`.  
  Chaves sem o prefixo `p_` causarão **404** no PostgREST.
- **Imagens de pessoas**: para exibir fotos em nós, o payload deve preencher `photo_url` (HTTP/HTTPS).  
  As colunas padrão podem ser configuradas via `.env`:
  - `MEMBERS_TABLE`, `MEMBERS_ID_COL`, `MEMBERS_PHOTO_COL`.
- **Cache**: habilite via `ENABLE_REDIS_CACHE=true` e configure `REDIS_URL`.  
  TTL de respostas do grafo em `CACHE_API_TTL` (segundos).
- **CORS**: variáveis `CORS_ALLOW_*` estão documentadas no `.env` e respeitadas em toda a aplicação.
- **Aparência**: cores especiais para **CV** (vermelho) e facções com **"PCC"** (azul-escuro) estão embutidas tanto no vis.js quanto no PyVis.

---

## Links úteis

- Repositório serviço: `svc-kg`  
- Exemplos PyVis/vis.js utilizados como referência:
  - `knowledge_graph` (exemplo de uso de grafo e construção de dados)
  - `pyvis` (exemplos de opções e melhores práticas)

---

[v1.7.20]: #
[v1.7.19]: #
[v1.7.18]: #
[v1.7.17]: #
[v1.7.13]: #
