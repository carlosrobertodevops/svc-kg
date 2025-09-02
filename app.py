# from __future__ import annotations

# import os
# from typing import Any, Dict, List, Optional, Set

# from fastapi import FastAPI, HTTPException, Query
# from fastapi.responses import ORJSONResponse
# from pydantic import BaseModel, Field

# # -----------------------------------------------------------------------------
# # Modelos
# # -----------------------------------------------------------------------------

# class Node(BaseModel):
#     id: str | int = Field(..., description="Identificador único do nó")
#     label: Optional[str] = None
#     data: Dict[str, Any] = Field(default_factory=dict)


# class Edge(BaseModel):
#     id: Optional[str | int] = None
#     source: str | int = Field(..., description="ID do nó origem")
#     target: str | int = Field(..., description="ID do nó destino")
#     label: Optional[str] = None
#     data: Dict[str, Any] = Field(default_factory=dict)


# class GraphPayload(BaseModel):
#     nodes: List[Node] = Field(default_factory=list)
#     edges: List[Edge] = Field(default_factory=list)


# class GraphResponse(BaseModel):
#     nodes: List[Node]
#     edges: List[Edge]
#     meta: Dict[str, Any] = Field(default_factory=dict)


# # -----------------------------------------------------------------------------
# # Funções utilitárias
# # -----------------------------------------------------------------------------

# def truncate_preview(
#     payload: GraphPayload,
#     max_nodes: int,
#     max_edges: int,
# ) -> GraphPayload:
#     """
#     Limita a quantidade de nós e arestas para pré-visualização.
#     - Mantém apenas nós até max_nodes.
#     - Filtra arestas para usar somente nós mantidos e corta em max_edges.
#     """
#     original_nodes = payload.nodes
#     original_edges = payload.edges

#     if max_nodes < 0 or max_edges < 0:
#         return payload  # nada a fazer

#     kept_nodes = original_nodes[:max_nodes] if len(original_nodes) > max_nodes else original_nodes
#     kept_ids: Set[str | int] = {n.id for n in kept_nodes}

#     filtered_edges = [e for e in original_edges if e.source in kept_ids and e.target in kept_ids]
#     kept_edges = filtered_edges[:max_edges] if len(filtered_edges) > max_edges else filtered_edges

#     return GraphPayload(nodes=kept_nodes, edges=kept_edges)


# def build_meta(before: GraphPayload, after: GraphPayload) -> Dict[str, Any]:
#     return {
#         "received_nodes": len(before.nodes),
#         "received_edges": len(before.edges),
#         "returned_nodes": len(after.nodes),
#         "returned_edges": len(after.edges),
#         "truncated": (len(after.nodes) < len(before.nodes)) or (len(after.edges) < len(before.edges)),
#     }


# # -----------------------------------------------------------------------------
# # App FastAPI
# # -----------------------------------------------------------------------------

# APP_NAME = os.getenv("APP_NAME", "svc-kg")
# DEFAULT_MAX_NODES = int(os.getenv("PREVIEW_MAX_NODES", "200"))
# DEFAULT_MAX_EDGES = int(os.getenv("PREVIEW_MAX_EDGES", "400"))

# app = FastAPI(
#     title=APP_NAME,
#     default_response_class=ORJSONResponse,
# )


# @app.get("/health")
# async def health() -> Dict[str, str]:
#     # Health check simples e confiável: não depende de atributos de pool
#     return {"status": "ok", "service": APP_NAME}


# @app.get("/")
# def root() -> Dict[str, Any]:
#     return {
#         "service": APP_NAME,
#         "endpoints": {
#             "GET /health": "status simples",
#             "POST /graph": "recebe grafo {nodes, edges}; usa preview opcional",
#         },
#     }


# @app.post("/graph", response_model=GraphResponse)
# def post_graph(
#     payload: GraphPayload,
#     preview: bool = Query(True, description="Se verdadeiro, limita nós/arestas para prévia."),
#     max_nodes: int = Query(DEFAULT_MAX_NODES, ge=0, description="Máximo de nós na prévia."),
#     max_edges: int = Query(DEFAULT_MAX_EDGES, ge=0, description="Máximo de arestas na prévia."),
# ) -> GraphResponse:
#     """
#     Recebe um grafo e, se preview=true, devolve versão reduzida.
#     """
#     if payload is None:
#         raise HTTPException(status_code=400, detail="Corpo inválido")

#     before = payload
#     if preview:
#         payload = truncate_preview(payload, max_nodes, max_edges)

#     return GraphResponse(nodes=payload.nodes, edges=payload.edges, meta=build_meta(before, payload))


# # -----------------------------------------------------------------------------
# # Suporte opcional a execução direta (dev). Em produção, use gunicorn/uvicorn.
# # -----------------------------------------------------------------------------
# if __name__ == "__main__":
#     import uvicorn

#     uvicorn.run(
#         "app:app",
#         host="0.0.0.0",
#         port=int(os.getenv("PORT", "8080")),
#         reload=bool(int(os.getenv("DEV_RELOAD", "0"))),
#     )

# --- imports (no topo, se ainda não tiver) ---
import os, json
from typing import Any, Dict, List, Optional
import httpx
from fastapi import Query
from fastapi.responses import HTMLResponse

SUPABASE_REST_URL = os.getenv("SUPABASE_REST_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_RPC_FUNCTION = os.getenv("SUPABASE_RPC_FUNCTION", "get_graph_membros")
SUPABASE_SCHEMA = os.getenv("SUPABASE_SCHEMA", "public")

async def call_supabase_rpc(fn: str, payload: Dict[str, Any]) -> Any:
    assert SUPABASE_REST_URL and SUPABASE_SERVICE_ROLE_KEY, "SUPABASE_* envs faltando"
    url = f"{SUPABASE_REST_URL}/rpc/{fn}"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Accept-Profile": SUPABASE_SCHEMA,
        "Content-Profile": SUPABASE_SCHEMA,
        "Prefer": "return=representation"
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()

def _coerce_to_graph(data: Any) -> Dict[str, Any]:
    """
    Suporta dois formatos comuns vindo do RPC:
    A) {"nodes":[...], "edges":[...]}
    B) [{"source":"...","target":"...","label":"..."}]  -> gera nodes únicos
    Ajuste os nomes dos campos conforme sua função.
    """
    if isinstance(data, dict) and "nodes" in data and "edges" in data:
        nodes = data.get("nodes") or []
        edges = data.get("edges") or []
    else:
        rows = data if isinstance(data, list) else []
        edges = []
        node_ids = {}
        for row in rows:
            s = str(row.get("source") or row.get("from") or row.get("membro_id") or "")
            t = str(row.get("target") or row.get("to")   or row.get("relacionado_id") or "")
            lab = row.get("label") or row.get("relacao") or row.get("tipo") or ""
            if s and t:
                edges.append({"source": s, "target": t, "label": str(lab)})
                node_ids.setdefault(s, {"id": s, "label": row.get("source_label") or s, "data": {}})
                node_ids.setdefault(t, {"id": t, "label": row.get("target_label") or t, "data": {}})
        nodes = list(node_ids.values())

    # tipagem simples por heurística (opcional)
    for n in nodes:
        if "data" not in n: n["data"] = {}
        if "tipo" not in n["data"]:
            nid = str(n.get("id","")).lower()
            if "facc" in nid: n["data"]["tipo"] = "faccao"
            elif "func" in nid: n["data"]["tipo"] = "funcao"
            else: n["data"]["tipo"] = "membro"
    return {"nodes": nodes, "edges": edges}

def _truncate_graph(nodes: List[Dict], edges: List[Dict], max_nodes: int, max_edges: int):
    if max_nodes and len(nodes) > max_nodes:
        keep = {n["id"] for n in nodes[:max_nodes]}
        nodes = nodes[:max_nodes]
        edges = [e for e in edges if e["source"] in keep and e["target"] in keep]
    if max_edges and len(edges) > max_edges:
        edges = edges[:max_edges]
    return nodes, edges

def _vis_html(nodes: List[Dict[str,Any]], edges: List[Dict[str,Any]], title="Grafo Membros") -> str:
    # HTML leve com vis-network (CDN). “Look & feel” similar ao pyVis.
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style> html,body,#net{{height:100%;margin:0;padding:0}} </style>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
</head><body>
<div id="net"></div>
<script>
const nodes = new vis.DataSet({json.dumps([{"id":n["id"],"label":n.get("label",""),"group":n.get("data",{}).get("tipo","membro")} for n in nodes])});
const edges = new vis.DataSet({json.dumps([{"from":e["source"],"to":e["target"],"label":e.get("label","")} for e in edges])});
const data = {{nodes, edges}};
const options = {{
  edges: {{arrows:'to', smooth:true}},
  physics: {{ stabilization: true }},
  interaction: {{ hover: true, tooltipDelay: 120 }},
  groups: {{
    membro: {{ shape:'dot', size:12 }},
    faccao: {{ shape:'triangle', size:16 }},
    funcao: {{ shape:'square', size:12 }}
  }}
}};
new vis.Network(document.getElementById('net'), data, options);
</script>
</body></html>"""

@app.get("/graph/membros")
async def graph_membros(
    membro_id: Optional[str] = Query(default=None),
    faccao_id: Optional[str] = Query(default=None),
    funcao: Optional[str] = Query(default=None),
    depth: int = Query(default=2, ge=0, le=6),
    preview: bool = Query(default=True),
    max_nodes: int = Query(default=500, ge=1),
    max_edges: int = Query(default=1000, ge=1),
):
    payload = {"membro_id": membro_id, "faccao_id": faccao_id, "funcao": funcao, "depth": depth}
    # remove None
    payload = {k:v for k,v in payload.items() if v is not None}
    raw = await call_supabase_rpc(SUPABASE_RPC_FUNCTION, payload)
    g = _coerce_to_graph(raw)
    nodes, edges = g["nodes"], g["edges"]
    truncated = False
    if preview:
        nodes, edges = _truncate_graph(nodes, edges, max_nodes, max_edges)
        truncated = (len(nodes) < len(g["nodes"])) or (len(edges) < len(g["edges"]))
    return {
        "nodes": nodes, "edges": edges,
        "meta": {
            "rpc": SUPABASE_RPC_FUNCTION,
            "params": payload,
            "truncated": truncated,
            "received_nodes": len(g["nodes"]),
            "received_edges": len(g["edges"]),
            "returned_nodes": len(nodes),
            "returned_edges": len(edges)
        }
    }

@app.get("/graph/membros/vis", response_class=HTMLResponse)
async def graph_membros_vis(
    membro_id: Optional[str] = None,
    faccao_id: Optional[str] = None,
    funcao: Optional[str] = None,
    depth: int = 2,
    preview: bool = True,
    max_nodes: int = 500,
    max_edges: int = 1000,
):
    payload = {"membro_id": membro_id, "faccao_id": faccao_id, "funcao": funcao, "depth": depth}
    payload = {k:v for k,v in payload.items() if v is not None}
    raw = await call_supabase_rpc(SUPABASE_RPC_FUNCTION, payload)
    g = _coerce_to_graph(raw)
    nodes, edges = g["nodes"], g["edges"]
    if preview:
        nodes, edges = _truncate_graph(nodes, edges, max_nodes, max_edges)
    return HTMLResponse(content=_vis_html(nodes, edges, title="Grafo Membros"), status_code=200)

@app.post("/rpc/get_graph_membros")
async def rpc_get_graph_membros(body: Dict[str, Any]):
    data = await call_supabase_rpc("get_graph_membros", body or {})
    return data
 