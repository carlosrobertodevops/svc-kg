from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field

# -----------------------------------------------------------------------------
# Modelos
# -----------------------------------------------------------------------------

class Node(BaseModel):
    id: str | int = Field(..., description="Identificador único do nó")
    label: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class Edge(BaseModel):
    id: Optional[str | int] = None
    source: str | int = Field(..., description="ID do nó origem")
    target: str | int = Field(..., description="ID do nó destino")
    label: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class GraphPayload(BaseModel):
    nodes: List[Node] = Field(default_factory=list)
    edges: List[Edge] = Field(default_factory=list)


class GraphResponse(BaseModel):
    nodes: List[Node]
    edges: List[Edge]
    meta: Dict[str, Any] = Field(default_factory=dict)


# -----------------------------------------------------------------------------
# Funções utilitárias
# -----------------------------------------------------------------------------

def truncate_preview(
    payload: GraphPayload,
    max_nodes: int,
    max_edges: int,
) -> GraphPayload:
    """
    Limita a quantidade de nós e arestas para pré-visualização.
    - Mantém apenas nós até max_nodes.
    - Filtra arestas para usar somente nós mantidos e corta em max_edges.
    """
    original_nodes = payload.nodes
    original_edges = payload.edges

    if max_nodes < 0 or max_edges < 0:
        return payload  # nada a fazer

    kept_nodes = original_nodes[:max_nodes] if len(original_nodes) > max_nodes else original_nodes
    kept_ids: Set[str | int] = {n.id for n in kept_nodes}

    filtered_edges = [e for e in original_edges if e.source in kept_ids and e.target in kept_ids]
    kept_edges = filtered_edges[:max_edges] if len(filtered_edges) > max_edges else filtered_edges

    return GraphPayload(nodes=kept_nodes, edges=kept_edges)


def build_meta(before: GraphPayload, after: GraphPayload) -> Dict[str, Any]:
    return {
        "received_nodes": len(before.nodes),
        "received_edges": len(before.edges),
        "returned_nodes": len(after.nodes),
        "returned_edges": len(after.edges),
        "truncated": (len(after.nodes) < len(before.nodes)) or (len(after.edges) < len(before.edges)),
    }


# -----------------------------------------------------------------------------
# App FastAPI
# -----------------------------------------------------------------------------

APP_NAME = os.getenv("APP_NAME", "svc-kg")
DEFAULT_MAX_NODES = int(os.getenv("PREVIEW_MAX_NODES", "200"))
DEFAULT_MAX_EDGES = int(os.getenv("PREVIEW_MAX_EDGES", "400"))

app = FastAPI(
    title=APP_NAME,
    default_response_class=ORJSONResponse,
)


@app.get("/health")
async def health() -> Dict[str, str]:
    # Health check simples e confiável: não depende de atributos de pool
    return {"status": "ok", "service": APP_NAME}


@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "service": APP_NAME,
        "endpoints": {
            "GET /health": "status simples",
            "POST /graph": "recebe grafo {nodes, edges}; usa preview opcional",
        },
    }


@app.post("/graph", response_model=GraphResponse)
def post_graph(
    payload: GraphPayload,
    preview: bool = Query(True, description="Se verdadeiro, limita nós/arestas para prévia."),
    max_nodes: int = Query(DEFAULT_MAX_NODES, ge=0, description="Máximo de nós na prévia."),
    max_edges: int = Query(DEFAULT_MAX_EDGES, ge=0, description="Máximo de arestas na prévia."),
) -> GraphResponse:
    """
    Recebe um grafo e, se preview=true, devolve versão reduzida.
    """
    if payload is None:
        raise HTTPException(status_code=400, detail="Corpo inválido")

    before = payload
    if preview:
        payload = truncate_preview(payload, max_nodes, max_edges)

    return GraphResponse(nodes=payload.nodes, edges=payload.edges, meta=build_meta(before, payload))


# -----------------------------------------------------------------------------
# Suporte opcional a execução direta (dev). Em produção, use gunicorn/uvicorn.
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        reload=bool(int(os.getenv("DEV_RELOAD", "0"))),
    )
