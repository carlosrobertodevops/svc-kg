import os
import json
import time
import hashlib
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager

# ============ Config ============
APP_NAME = os.getenv("APP_NAME", "svc-kg")
SERVICE_PORT = int(os.getenv("SERVICE_PORT", "8080"))

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

ALLOW_ORIGINS = [
    o.strip() for o in os.getenv("ALLOW_ORIGINS", "*").split(",")
] if os.getenv("ALLOW_ORIGINS") else ["*"]

CACHE_TTL_SECONDS = int(os.getenv("SVC_CACHE_TTL_SECONDS", "60"))

# ============ Cache (TTL in-memory) ============
class _TTLCache:
    def __init__(self, ttl: int):
        self.ttl = ttl
        self._store: Dict[str, Tuple[float, Any]] = {}

    def _now(self) -> float:
        return time.time()

    def get(self, key: str) -> Optional[Any]:
        item = self._store.get(key)
        if not item:
            return None
        expires_at, value = item
        if self._now() > expires_at:
            try:
                del self._store[key]
            except KeyError:
                pass
            return None
        return value

    def set(self, key: str, value: Any):
        self._store[key] = (self._now() + self.ttl, value)

CACHE = _TTLCache(CACHE_TTL_SECONDS)

def _cache_key(prefix: str, payload: Dict[str, Any]) -> str:
    s = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return f"{prefix}:{hashlib.sha256(s.encode('utf-8')).hexdigest()}"

# ============ HTTP Client (global) ============
http_client: Optional[httpx.AsyncClient] = None

@asynccontextmanager
async def lifespan(_: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
    try:
        yield
    finally:
        try:
            await http_client.aclose()
        except Exception:
            pass

app = FastAPI(
    title="svc-kg",
    description="Microserviço de grafos (pyVis) com dados do Supabase RPC.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============ Modelos ============
class GraphNode(BaseModel):
    id: str
    label: Optional[str] = None
    group: Optional[str] = None
    title: Optional[str] = None

class GraphEdge(BaseModel):
    source: str
    target: str
    label: Optional[str] = None

class GraphPayload(BaseModel):
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    meta: Optional[Dict[str, Any]] = None

# ============ Utils de normalização ============
def _to_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in ("1", "true", "t", "yes", "y", "on")

def _ensure_nodes_edges(data: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Normaliza quaisquer formatos plausíveis vindos da função RPC para:
      nodes: [{id, label?, group?, title?}]
      edges: [{source, target, label?}]
    """
    if isinstance(data, dict) and "nodes" in data and "edges" in data:
        raw_nodes = data["nodes"] or []
        raw_edges = data["edges"] or []
    else:
        # fallback: se vier como lista de relacionamentos, infere nós
        raw = data or []
        raw_nodes = []
        raw_edges = []
        seen = set()
        for item in raw:
            # tenta várias chaves comuns
            s = item.get("from") or item.get("source") or item.get("src") or item.get("a")
            t = item.get("to") or item.get("target") or item.get("dst") or item.get("b")
            lab = item.get("label") or item.get("rel") or item.get("type")
            if s is None or t is None:
                continue
            raw_edges.append({"source": str(s), "target": str(t), "label": lab})
            if s not in seen:
                raw_nodes.append({"id": str(s)})
                seen.add(s)
            if t not in seen:
                raw_nodes.append({"id": str(t)})
                seen.add(t)

    # normaliza nodes
    nodes: List[Dict[str, Any]] = []
    for n in raw_nodes:
        nid = str(n.get("id") or n.get("node_id") or n.get("key") or n.get("name"))
        if not nid:
            # ignora nodes sem id
            continue
        nodes.append({
            "id": nid,
            "label": n.get("label") or n.get("name") or nid,
            "group": n.get("group") or n.get("type"),
            "title": n.get("title") or n.get("hint") or None,
        })

    # normaliza edges
    edges: List[Dict[str, Any]] = []
    for e in raw_edges:
        s = e.get("source") or e.get("from") or e.get("src") or e.get("a")
        t = e.get("target") or e.get("to") or e.get("dst") or e.get("b")
        if s is None or t is None:
            continue
        edges.append({
            "source": str(s),
            "target": str(t),
            "label": e.get("label") or e.get("type"),
        })

    return nodes, edges

def _truncate(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], max_nodes: int, max_edges: int):
    nn = nodes[:max_nodes] if max_nodes > 0 else nodes
    allowed = {n["id"] for n in nn}
    ee = [e for e in edges if e["source"] in allowed and e["target"] in allowed]
    if max_edges > 0 and len(ee) > max_edges:
        ee = ee[:max_edges]
    return nn, ee

# ============ Supabase RPC ============
async def _fetch_graph_from_supabase(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(status_code=500, detail="Supabase não configurado (SUPABASE_URL/KEY).")

    url = f"{SUPABASE_URL}/rest/v1/rpc/get_graph_membros"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

    assert http_client is not None, "http_client não inicializado"
    resp = await http_client.post(url, headers=headers, json=payload)
    if resp.status_code >= 500:
        raise HTTPException(status_code=503, detail=f"Falha no RPC Supabase ({resp.status_code}).")
    if resp.status_code >= 400:
        # devolve erro do RPC (ex.: parâmetro inválido)
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail={"rpc_error": detail})

    try:
        return resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Resposta do RPC não é JSON.")

# ============ PyVis HTML ============
def _pyvis_html(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], title: str = "Grafo") -> str:
    # Import local (evita custo em /health)
    from pyvis.network import Network

    net = Network(height="100%", width="100%", bgcolor="#111111", font_color="#ffffff", directed=True)
    net.barnes_hut()  # física padrão

    # adiciona nós
    for n in nodes:
        net.add_node(
            n["id"],
            label=n.get("label") or n["id"],
            title=n.get("title"),
            group=n.get("group"),
        )

    # adiciona arestas
    for e in edges:
        net.add_edge(e["source"], e["target"], title=e.get("label"))

    net.set_options("""
    const options = {
      nodes: { shape: "dot", size: 14 },
      edges: { arrows: { to: { enabled: true, scaleFactor: 0.6 }}, smooth: false },
      physics: { stabilization: { iterations: 200 } },
      interaction: { hover: true, tooltipDelay: 200, hideEdgesOnDrag: false }
    }
    """)

    html = net.generate_html(notebook=False)
    # Título simples no body (o pyvis sobrescreve head/body, então fazemos append)
    html = html.replace("</body>", f"<div style='position:fixed;left:12px;top:8px;color:#fff;font-family:sans-serif;font-size:14px;opacity:.7'>{title}</div></body>")
    return html

# ============ Endpoints ============
@app.get("/health")
async def health():
    # Mantenha simples para ficar sempre 'healthy'
    status = {"status": "ok"}
    try:
        status["http_client_ok"] = (http_client is not None) and (not http_client.is_closed)  # type: ignore[attr-defined]
    except Exception:
        status["http_client_ok"] = False
    return JSONResponse(status_code=200, content=status)

@app.get("/graph/members", response_model=GraphPayload, responses={502: {"description": "Erro no RPC"}})
async def graph_members(
    p_faccao_id: str = Query(..., description="ID da facção"),
    p_include_co: bool = Query(True, description="Incluir conexões co-ocorrentes"),
    p_max_pairs: int = Query(500, ge=1, le=20000, description="Máximo de pares/arestas retornados pelo RPC"),
    max_nodes: int = Query(0, ge=0, description="Corte local de nós (0 = sem corte)"),
    max_edges: int = Query(0, ge=0, description="Corte local de arestas (0 = sem corte)"),
):
    payload = {
        "p_faccao_id": p_faccao_id,
        "p_include_co": p_include_co,
        "p_max_pairs": p_max_pairs,
    }
    key = _cache_key("members", {**payload, "max_nodes": max_nodes, "max_edges": max_edges})
    cached = CACHE.get(key)
    if cached is not None:
        return cached

    data = await _fetch_graph_from_supabase(payload)
    nodes, edges = _ensure_nodes_edges(data)
    if max_nodes or max_edges:
        nodes, edges = _truncate(nodes, edges, max_nodes, max_edges)

    result: Dict[str, Any] = {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "count_nodes": len(nodes),
            "count_edges": len(edges),
            "faccao_id": p_faccao_id,
            "include_co": p_include_co,
            "max_pairs": p_max_pairs,
            "cached": False,
        }
    }
    CACHE.set(key, result)
    return result

@app.get("/graph/members/html", response_class=HTMLResponse)
async def graph_members_html(
    p_faccao_id: str = Query(..., description="ID da facção"),
    p_include_co: bool = Query(True, description="Incluir conexões co-ocorrentes"),
    p_max_pairs: int = Query(500, ge=1, le=20000, description="Máximo de pares/arestas"),
    max_nodes: int = Query(0, ge=0, description="Corte de nós (0 = sem corte)"),
    max_edges: int = Query(0, ge=0, description="Corte de arestas (0 = sem corte)"),
    title: str = Query("Relações: Membros x Facções x Funções", description="Título do gráfico"),
):
    payload = {
        "p_faccao_id": p_faccao_id,
        "p_include_co": p_include_co,
        "p_max_pairs": p_max_pairs,
    }
    key = _cache_key("members_html", {**payload, "max_nodes": max_nodes, "max_edges": max_edges, "title": title})
    cached = CACHE.get(key)
    if cached is not None:
        return HTMLResponse(content=cached, status_code=200)

    data = await _fetch_graph_from_supabase(payload)
    nodes, edges = _ensure_nodes_edges(data)
    if max_nodes or max_edges:
        nodes, edges = _truncate(nodes, edges, max_nodes, max_edges)

    html = _pyvis_html(nodes, edges, title=title)
    CACHE.set(key, html)
    return HTMLResponse(content=html, status_code=200)

@app.get("/", response_class=PlainTextResponse, include_in_schema=False)
async def root():
    return f"{APP_NAME} up. Veja /docs para Swagger e /graph/members ou /graph/members/html."

# ========= Exec local =========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=SERVICE_PORT, reload=True)
