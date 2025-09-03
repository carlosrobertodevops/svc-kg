
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


---

## 🔎 Checklist agora (externo)

1. Abra no browser:  
   `https://svc-kg.mondaha.com/live`  
   Esperado: `200 {"status":"live",...}`

2. Se (1) estiver ok, teste:  
   `https://svc-kg.mondaha.com/health`  
   Deve vir `200`.

3. Depois:  
   `https://svc-kg.mondaha.com/ready`  
   - `200`: tudo certo (Redis/backend ok).  
   - `503`: a resposta JSON aponta o que está falhando (ver campo `error`).

4. Por fim, a rota do grafo:  
   `https://svc-kg.mondaha.com/v1/graph/membros?faccao_id=6&include_co=true&max_pairs=500`

Se **(1)** ainda der **503**, o problema é 100% de **roteamento/porta no Coolify** (o Traefik não encontra container saudável na porta interna). A correção é alinhar **PORT** + **Application Port** + (opcional) **labels** acima.
::contentReference[oaicite:0]{index=0}



## Troubleshooting 503 ("no available server")

Esse 503 vem do Traefik/edge. Siga:

1) **App port interno**
   - No Coolify, verifique **Application Port** do serviço. Deve ser **8080** (ou altere `PORT` nas envs do app para o valor da UI).
   - Nosso container escuta em `0.0.0.0:${PORT:-8080}`.

2) **Healthcheck no Coolify**
   - Use `/live` (não depende de Redis/DB).
   - Se o health estiver falhando, o Traefik não publica o serviço.

3) **Teste direto do app (no host do container)**
   ```bash
   docker exec -it <container-svc-kg> sh -lc 'curl -sS http://localhost:${PORT:-8080}/live && echo'
   docker exec -it <container-svc-kg> sh -lc 'curl -sS http://localhost:${PORT:-8080}/ready && echo'

