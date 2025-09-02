import os
import json
import time
import hashlib
from typing import Any, Dict, List, Optional, Tuple
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel

# Static files
from starlette.staticfiles import StaticFiles

# Redis (opcional)
try:
    from redis.asyncio import Redis
except Exception:
    Redis = None  # type: ignore

# ===================== ENV & Config =====================
APP_NAME = os.getenv("APP_NAME", "svc-kg")

# Porta é controlada pelo Gunicorn no container; aqui só para execução local.
SERVICE_PORT = int(os.getenv("PORT", os.getenv("SERVICE_PORT", "8080")))

# CORS
def _split_csv(val: Optional[str]) -> List[str]:
    if not val:
        return []
    return [x.strip() for x in val.split(",") if x.strip()]

def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "t", "yes", "y", "on")

CORS_ALLOW_ORIGINS = _split_csv(os.getenv("CORS_ALLOW_ORIGINS", "*"))
CORS_ALLOW_HEADERS = _split_csv(os.getenv("CORS_ALLOW_HEADERS", "Authorization,Content-Type"))
CORS_ALLOW_METHODS = _split_csv(os.getenv("CORS_ALLOW_METHODS", "GET,POST,OPTIONS"))
CORS_ALLOW_CREDENTIALS = _env_bool("CORS_ALLOW_CREDENTIALS", False)

# Supabase
SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_RPC_FN = os.getenv("SUPABASE_RPC_FN", "get_graph_membros")
SUPABASE_TIMEOUT = float(os.getenv("SUPABASE_TIMEOUT", "15"))

# Cache
CACHE_API_TTL = int(os.getenv("CACHE_API_TTL", "60"))  # TTL do cache de API (segundos)
CACHE_STATIC_MAX_AGE = int(os.getenv("CACHE_STATIC_MAX_AGE", "86400"))  # para HTML/YAML estáticos
ENABLE_REDIS_CACHE = _env_bool("ENABLE_REDIS_CACHE", True)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# ===================== Cache de Memória TTL =====================
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

def _cache_key(prefix: str, payload: Dict[str, Any]) -> str:
    s = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return f"{prefix}:{hashlib.sha256(s.encode('utf-8')).hexdigest()}"

MEM_CACHE = _TTLCache(CACHE_API_TTL)

# ===================== HTTP & Redis clients =====================
http_client: Optional[httpx.AsyncClient] = None
redis_client: Optional["Redis"] = None  # type: ignore

async def cache_get(key: str) -> Optional[Any]:
    # 1) memória
    v = MEM_CACHE.get(key)
    if v is not None:
        return v
    # 2) redis (opcional)
    if redis_client is not None:
        try:
            raw = await redis_client.get(key)  # type: ignore
            if raw:
                v = json.loads(raw)
                MEM_CACHE.set(key, v)  # aquece memória
                return v
        except Exception:
            pass
    return None

async def cache_set(key: str, value: Any):
    MEM_CACHE.set(key, value)
    if redis_client is not None:
        try:
            await redis_client.setex(key, CACHE_API_TTL, json.dumps(value, ensure_ascii=False))  # type: ignore
        except Exception:
            pass

@asynccontextmanager
async def lifespan(_: FastAPI):
    global http_client, redis_client
    # httpx com timeout vindo do env
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(SUPABASE_TIMEOUT, connect=5.0))
    # Redis (opcional)
    if ENABLE_REDIS_CACHE and Redis is not None:
        try:
            redis_client = Redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)  # type: ignore
            await redis_client.ping()  # valida conexão
        except Exception:
            redis_client = None
    try:
        yield
    finally:
        try:
            if http_client:
                await http_client.aclose()
        except Exception:
            pass
        try:
            if redis_client:
                await redis_client.close()
        except Exception:
            pass

app = FastAPI(
    title="svc-kg",
    description="Microserviço de grafos (pyVis) consumindo Supabase RPC.",
    version="1.1.0",
    lifespan=lifespan,
)

# Middlewares
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS if CORS_ALLOW_ORIGINS else ["*"],
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=CORS_ALLOW_METHODS if CORS_ALLOW_METHODS else ["*"],
    allow_headers=CORS_ALLOW_HEADERS if CORS_ALLOW_HEADERS else ["*"],
)

# Static mounts (se existirem as pastas)
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static", html=False), name="static")
if os.path.isdir("assets"):
    app.mount("/assets", StaticFiles(directory="assets", html=False), name="assets")

# ===================== Modelos =====================
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

# ===================== Utils =====================
def _ensure_nodes_edges(data: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Normaliza o payload do RPC para:
      nodes: [{id, label?, group?, title?}]
      edges: [{source, target, label?}]
    """
    if isinstance(data, dict) and "nodes" in data and "edges" in data:
        raw_nodes = data.get("nodes") or []
        raw_edges = data.get("edges") or []
    else:
        raw = data or []
        raw_nodes = []
        raw_edges = []
        seen = set()
        for item in raw:
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

    nodes: List[Dict[str, Any]] = []
    for n in raw_nodes:
        nid = str(n.get("id") or n.get("node_id") or n.get("key") or n.get("name") or "")
        if not nid:
            continue
        nodes.append({
            "id": nid,
            "label": n.get("label") or n.get("name") or nid,
            "group": n.get("group") or n.get("type"),
            "title": n.get("title") or n.get("hint"),
        })

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

def _cache_hdr(max_age: int) -> Dict[str, str]:
    return {"Cache-Control": f"public, max-age={max_age}, stale-while-revalidate=60"}

# ===================== Supabase RPC =====================
async def _fetch_graph_from_supabase(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(status_code=500, detail="Supabase não configurado (SUPABASE_URL/KEY).")

    url = f"{SUPABASE_URL}/rest/v1/rpc/{SUPABASE_RPC_FN}"
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
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail={"rpc_error": detail})

    try:
        return resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Resposta do RPC não é JSON.")

# ===================== PyVis HTML =====================
def _pyvis_html(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], title: str = "Grafo") -> str:
    from pyvis.network import Network

    net = Network(height="100%", width="100%", bgcolor="#111111", font_color="#ffffff", directed=True)
    net.barnes_hut()

    for n in nodes:
        net.add_node(
            n["id"],
            label=n.get("label") or n["id"],
            title=n.get("title"),
            group=n.get("group"),
        )
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
    html = html.replace("</body>", f"<div style='position:fixed;left:12px;top:8px;color:#fff;font-family:sans-serif;font-size:14px;opacity:.7'>{title}</div></body>")
    return html

# ===================== Endpoints =====================
@app.get("/health")
async def health():
    status = {
        "status": "ok",
        "http_client_ok": bool(http_client and not getattr(http_client, "is_closed", False)),
        "redis_enabled": bool(ENABLE_REDIS_CACHE and Redis is not None),
        "redis_ok": False,
        "supabase_url": bool(SUPABASE_URL),
        "rpc_fn": SUPABASE_RPC_FN,
    }
    if redis_client is not None:
        try:
            pong = await redis_client.ping()  # type: ignore
            status["redis_ok"] = bool(pong)
        except Exception:
            status["redis_ok"] = False
    return JSONResponse(status_code=200, content=status, headers=_cache_hdr(0))

@app.get("/openapi.yaml", response_class=PlainTextResponse, include_in_schema=False)
async def openapi_yaml():
    path = os.path.join("docs", "openapi.yaml")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="openapi.yaml não encontrado em /docs")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return PlainTextResponse(content, media_type="text/yaml; charset=utf-8", headers=_cache_hdr(CACHE_STATIC_MAX_AGE))

@app.get("/graph/members", response_model=GraphPayload)
async def graph_members(
    p_faccao_id: str = Query(..., description="ID da facção"),
    p_include_co: bool = Query(True, description="Incluir co-ocorrências"),
    p_max_pairs: int = Query(500, ge=1, le=20000, description="Limite de pares no RPC"),
    max_nodes: int = Query(0, ge=0, description="Corte local de nós (0=sem corte)"),
    max_edges: int = Query(0, ge=0, description="Corte local de arestas (0=sem corte)"),
):
    payload = {"p_faccao_id": p_faccao_id, "p_include_co": p_include_co, "p_max_pairs": p_max_pairs}
    key = _cache_key("members", {**payload, "max_nodes": max_nodes, "max_edges": max_edges})

    cached = await cache_get(key)
    if cached is not None:
        return JSONResponse(cached, headers=_cache_hdr(CACHE_API_TTL))

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
    await cache_set(key, result)
    return JSONResponse(result, headers=_cache_hdr(CACHE_API_TTL))

@app.get("/graph/members/html", response_class=HTMLResponse)
async def graph_members_html(
    p_faccao_id: str = Query(..., description="ID da facção"),
    p_include_co: bool = Query(True, description="Incluir co-ocorrências"),
    p_max_pairs: int = Query(500, ge=1, le=20000, description="Limite de pares no RPC"),
    max_nodes: int = Query(0, ge=0, description="Corte local de nós (0=sem corte)"),
    max_edges: int = Query(0, ge=0, description="Corte local de arestas (0=sem corte)"),
    title: str = Query("Relações: Membros x Facções x Funções", description="Título"),
):
    payload = {"p_faccao_id": p_faccao_id, "p_include_co": p_include_co, "p_max_pairs": p_max_pairs}
    key = _cache_key("members_html", {**payload, "max_nodes": max_nodes, "max_edges": max_edges, "title": title})

    cached = await cache_get(key)
    if cached is not None:
        return HTMLResponse(cached, headers=_cache_hdr(CACHE_STATIC_MAX_AGE))

    data = await _fetch_graph_from_supabase(payload)
    nodes, edges = _ensure_nodes_edges(data)
    if max_nodes or max_edges:
        nodes, edges = _truncate(nodes, edges, max_nodes, max_edges)

    html = _pyvis_html(nodes, edges, title=title)
    await cache_set(key, html)
    return HTMLResponse(html, headers=_cache_hdr(CACHE_STATIC_MAX_AGE))

@app.get("/", response_class=PlainTextResponse, include_in_schema=False)
async def root():
    return f"{APP_NAME} up. Veja /docs (Swagger), /graph/members e /graph/members/html."

# Execução local (sem gunicorn)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=SERVICE_PORT, reload=True)
