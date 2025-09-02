# app.py
import uvicorn
from fastapi import FastAPI, Query, Body, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from typing import Any, Dict, Optional
from src.settings import settings
from src.supabase_client import rpc_client
from src.graph_utils import normalize_graph, truncate_preview, build_pyvis_html
from src.cache import cache

app = FastAPI(title="svc-kg", version="1.1.0")

# --- CORS ---
allow_origins = settings.cors_allow_origins
if allow_origins == ["*"]:
    allow_credentials = False
else:
    allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _cache_control_value() -> str:
    swr = settings.http_stale_while_revalidate_seconds
    base = f"public, max-age={settings.http_cache_seconds}"
    return f"{base}, stale-while-revalidate={swr}" if swr > 0 else base

@app.get("/", response_class=PlainTextResponse)
async def root():
    return "svc-kg up. veja /docs"

@app.get("/health")
async def health(deep: bool = Query(default=False)):
    info = {
        "status": "ok",
        "version": app.version,
        "supabase_url": settings.supabase_url or "",
        "rpc_function": settings.supabase_rpc_function,
    }
    if deep:
        ok, detail = await rpc_client.ping()
        info["supabase"] = {"ok": ok, "detail": detail}
        return JSONResponse(info, status_code=200 if ok else 503)
    return JSONResponse(info)

@app.get("/graph/membros")
async def get_graph_membros(
    p_faccao_id: Optional[str] = Query(default=None),
    p_include_co: Optional[bool] = Query(default=None),
    p_max_pairs: Optional[int] = Query(default=None),
    depth: Optional[int] = Query(default=None),
    preview: bool = Query(default=True),
    max_nodes: int = Query(default=500, ge=10, le=5000),
    max_edges: int = Query(default=1000, ge=10, le=20000),
    nocache: bool = Query(default=False),
    cache_ttl: Optional[int] = Query(default=None),
) -> JSONResponse:
    payload: Dict[str, Any] = {}
    if p_faccao_id is not None: payload["p_faccao_id"] = p_faccao_id
    if p_include_co is not None: payload["p_include_co"] = p_include_co
    if p_max_pairs is not None: payload["p_max_pairs"] = p_max_pairs
    if depth is not None: payload["depth"] = depth

    key = cache.key_for(settings.supabase_rpc_function, payload)
    hit = False
    raw = None

    if not nocache:
        raw = await cache.get(key)
        if raw is not None:
            hit = True

    if raw is None:
        lock = await cache.acquire_key_lock(key)
        async with lock:
            raw = await cache.get(key)
            if raw is None:
                raw = await rpc_client.call_rpc(settings.supabase_rpc_function, payload)
                await cache.set(key, raw, ttl=cache_ttl)

    graph = normalize_graph(raw)
    returned = graph
    truncated = False
    if preview:
        returned, truncated = truncate_preview(graph, max_nodes=max_nodes, max_edges=max_edges)

    meta = {
        "rpc": settings.supabase_rpc_function,
        "params": payload,
        "truncated": truncated,
        "received_nodes": len(graph.get("nodes", [])),
        "received_edges": len(graph.get("edges", [])),
        "returned_nodes": len(returned.get("nodes", [])),
        "returned_edges": len(returned.get("edges", [])),
        "cache": "HIT" if hit else "MISS",
    }
    returned["meta"] = meta

    resp = JSONResponse(returned)
    resp.headers["Cache-Control"] = _cache_control_value()
    resp.headers["X-Cache"] = "HIT" if hit else "MISS"
    return resp

@app.get("/graph/membros/vis", response_class=HTMLResponse)
async def get_graph_membros_vis(
    p_faccao_id: Optional[str] = Query(default=None),
    p_include_co: Optional[bool] = Query(default=None),
    p_max_pairs: Optional[int] = Query(default=None),
    depth: Optional[int] = Query(default=None),
    preview: bool = Query(default=True),
    max_nodes: int = Query(default=500, ge=10, le=5000),
    max_edges: int = Query(default=1000, ge=10, le=20000),
    physics: bool = Query(default=True),
    nocache: bool = Query(default=False),
    cache_ttl: Optional[int] = Query(default=None),
) -> HTMLResponse:
    payload: Dict[str, Any] = {}
    if p_faccao_id is not None: payload["p_faccao_id"] = p_faccao_id
    if p_include_co is not None: payload["p_include_co"] = p_include_co
    if p_max_pairs is not None: payload["p_max_pairs"] = p_max_pairs
    if depth is not None: payload["depth"] = depth

    key = cache.key_for(settings.supabase_rpc_function, payload)
    hit = False
    raw = None

    if not nocache:
        raw = await cache.get(key)
        if raw is not None:
            hit = True

    if raw is None:
        lock = await cache.acquire_key_lock(key)
        async with lock:
            raw = await cache.get(key)
            if raw is None:
                raw = await rpc_client.call_rpc(settings.supabase_rpc_function, payload)
                await cache.set(key, raw, ttl=cache_ttl)

    graph = normalize_graph(raw)
    if preview:
        graph, _ = truncate_preview(graph, max_nodes=max_nodes, max_edges=max_edges)
    html = build_pyvis_html(graph, physics=physics, height="100%", width="100%")

    resp = HTMLResponse(html)
    resp.headers["Cache-Control"] = _cache_control_value()
    resp.headers["X-Cache"] = "HIT" if hit else "MISS"
    return resp

@app.post("/rpc/get_graph_membros")
async def rpc_get_graph_membros(body: Dict[str, Any] = Body(default=None)):
    try:
        result = await rpc_client.call_rpc(settings.supabase_rpc_function, body or {})
        return JSONResponse(result)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=False)
