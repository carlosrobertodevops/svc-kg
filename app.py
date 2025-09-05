# =============================================================================
# Arquivo: app.py
# Versão: v1.7.20 (ajuste visual: arestas ultrafinas + destaque de busca no /pyvis)
# Objetivo: API FastAPI do micro-serviço svc-kg (graph + visualizações + ops)
# Funções/métodos:
# - live/health/ready/ops_status: sondas e status operacional
# - graph_membros: retorna grafo (nós/arestas) via Supabase RPC
# - vis_visjs: página HTML com vis-network (dados embutidos, usa static/vis-embed.js)
# - vis_pyvis: página HTML com PyVis (arestas ultrafinas, física off pós-estabilização, busca com destaque)
# - Utilidades: normalização de labels de array PG, cache Redis, truncamento seguro
# =============================================================================
import os
import json
import asyncio
import logging
import socket
from typing import Optional, Dict, Any, List

import httpx
from fastapi import FastAPI, Query, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pyvis.network import Network

try:
    from redis import asyncio as aioredis  # redis==5
except Exception:
    aioredis = None

# -----------------------------------------------------------------------------
# Config & logger
# -----------------------------------------------------------------------------
APP_ENV = os.getenv("APP_ENV", "production")
PORT = int(os.getenv("PORT", "8080"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "info").upper()

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
log = logging.getLogger("svc-kg")

# CORS
CORS_ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*")
CORS_ALLOW_CREDENTIALS = os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true"
CORS_ALLOW_HEADERS = os.getenv("CORS_ALLOW_HEADERS", "Authorization,Content-Type")
CORS_ALLOW_METHODS = os.getenv("CORS_ALLOW_METHODS", "GET,POST,OPTIONS")

# Backend: Supabase/PostgREST
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_KEY = (
    os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    or os.getenv("SUPABASE_KEY", "").strip()
    or os.getenv("SUPABASE_ANON_KEY", "").strip()
)
SUPABASE_RPC_FN = os.getenv("SUPABASE_RPC_FN", "get_graph_membros")
SUPABASE_TIMEOUT = float(os.getenv("SUPABASE_TIMEOUT", "15"))

# Cache
ENABLE_REDIS_CACHE = os.getenv("ENABLE_REDIS_CACHE", "false").lower() == "true"
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CACHE_API_TTL = int(os.getenv("CACHE_API_TTL", "60"))
CACHE_STATIC_MAX_AGE = int(os.getenv("CACHE_STATIC_MAX_AGE", "86400"))

# Metadata
SERVICE_ID = "svc-kg"
SERVICE_AKA = ["sic-kg"]

# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------
app = FastAPI(
    title="svc-kg",
    version="v1.7.20",
    description="Micro serviço de Knowledge Graph com visualizações (vis.js e PyVis).",
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
)

# Static mounts
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
if os.path.isdir("docs"):
    app.mount("/docs-static", StaticFiles(directory="docs"), name="docs-static")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ALLOW_ORIGINS.split(",")] if CORS_ALLOW_ORIGINS != "*" else ["*"],
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=[m.strip() for m in CORS_ALLOW_METHODS.split(",")],
    allow_headers=[h.strip() for h in CORS_ALLOW_HEADERS.split(",")],
)

# -----------------------------------------------------------------------------
# Globals
# -----------------------------------------------------------------------------
_http: Optional[httpx.AsyncClient] = None
_redis = None  # type: ignore

# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------
def _env_backend_ok() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY and SUPABASE_RPC_FN)

async def _get_http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=SUPABASE_TIMEOUT)
    return _http

async def _get_redis():
    global _redis
    if not ENABLE_REDIS_CACHE or aioredis is None:
        return None
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    return _redis

def _cache_key(prefix: str, params: Dict[str, Any]) -> str:
    blob = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return f"kg:{prefix}:{hash(blob)}"

def _normalize_pg_text_array_label(s: str) -> str:
    if not s:
        return s
    s2 = s.strip()
    if len(s2) >= 2 and s2[0] == "{" and s2[-1] == "}":
        inner = s2[1:-1]
        if not inner:
            return ""
        parts = [p.strip().strip('"') for p in inner.split(",")]
        parts = [p for p in parts if p and p.lower() != "null"]
        return ", ".join(parts)
    return s

def normalize_graph_labels(data: Dict[str, Any]) -> Dict[str, Any]:
    nodes = data.get("nodes", []) or []
    edges = data.get("edges", []) or []

    fixed_nodes = []
    node_ids = set()
    for n in nodes:
        if not n or "id" not in n:
            continue
        nid = str(n["id"])
        node_ids.add(nid)
        label = n.get("label")
        if isinstance(label, str):
            label = _normalize_pg_text_array_label(label)
        fixed = dict(n)
        fixed["id"] = nid
        if label is not None:
            fixed["label"] = label
        fixed_nodes.append(fixed)

    fixed_edges = []
    for e in edges:
        if not e:
            continue
        a = str(e.get("source"))
        b = str(e.get("target"))
        if a in node_ids and b in node_ids:
            fe = dict(e)
            fe["source"] = a
            fe["target"] = b
            fixed_edges.append(fe)

    return {"nodes": fixed_nodes, "edges": fixed_edges}

def truncate_preview(data: Dict[str, Any], max_nodes: int, max_edges: int) -> Dict[str, Any]:
    ns = data.get("nodes", [])[: max(0, max_nodes)]
    idset = {str(n["id"]) for n in ns if n and "id" in n}
    es = [e for e in (data.get("edges", []) or []) if e and str(e.get("source")) in idset and str(e.get("target")) in idset]
    es = es[: max(0, max_edges)]
    return {"nodes": ns, "edges": es}

async def supabase_rpc_get_graph(faccao_id: Optional[int], include_co: bool, max_pairs: int) -> Dict[str, Any]:
    if not _env_backend_ok():
        raise RuntimeError("backend_not_configured: defina SUPABASE_URL e SUPABASE_SERVICE_KEY")

    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/rpc/{SUPABASE_RPC_FN}"
    payload = {
        # enviamos dois formatos (compatibilidade PostgREST vs Supabase)
        "faccao_id": faccao_id,
        "include_co": include_co,
        "max_pairs": max_pairs,
        "p_faccao_id": faccao_id,
        "p_include_co": include_co,
        "p_max_pairs": max_pairs,
    }
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    client = await _get_http()
    resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"{resp.status_code}: Supabase RPC {SUPABASE_RPC_FN} falhou: {resp.text}")
    data = resp.json()
    if not isinstance(data, dict):
        if isinstance(data, list) and data and isinstance(data[0], dict) and "nodes" in data[0]:
            data = data[0]
        else:
            raise RuntimeError("Formato inesperado do RPC (esperado objeto com nodes/edges)")
    return data

async def fetch_graph_sanitized(faccao_id: Optional[int], include_co: bool, max_pairs: int, use_cache: bool = True) -> Dict[str, Any]:
    cache_key = _cache_key("graph", {"faccao_id": faccao_id, "include_co": include_co, "max_pairs": max_pairs})
    if use_cache:
        r = await _get_redis()
        if r:
            cached = await r.get(cache_key)
            if cached:
                try:
                    return json.loads(cached)
                except Exception:
                    pass

    raw = await supabase_rpc_get_graph(faccao_id, include_co, max_pairs)
    fixed = normalize_graph_labels(raw)

    if use_cache:
        r = await _get_redis()
        if r:
            await r.set(cache_key, json.dumps(fixed, ensure_ascii=False), ex=CACHE_API_TTL)
    return fixed

def redact(token: Optional[str], keep: int = 4) -> Optional[str]:
    if not token:
        return token
    if len(token) <= keep:
        return "*" * len(token)
    return "*" * (len(token) - keep) + token[-keep:]

def running_in_container() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "rt") as fh:
            txt = fh.read()
        return "docker" in txt or "kubepods" in txt
    except Exception:
        return False

def platform_info() -> Dict[str, Any]:
    return {
        "hostname": socket.gethostname(),
        "container": running_in_container(),
        "coolify_proxy_network": os.getenv("COOLIFY_PROXY_NETWORK") or None,
        "app_env": APP_ENV,
        "service_id": SERVICE_ID,
        "aka": SERVICE_AKA,
        "version": app.version,
    }

# -----------------------------------------------------------------------------
# Lifecycle
# -----------------------------------------------------------------------------
@app.on_event("startup")
async def _startup():
    await _get_http()
    if ENABLE_REDIS_CACHE and aioredis:
        await _get_redis()
    log.info("svc-kg iniciado (backend: %s, cache: %s)", "supabase" if _env_backend_ok() else "none", "redis" if ENABLE_REDIS_CACHE else "none")

@app.on_event("shutdown")
async def _shutdown():
    global _http, _redis
    if _http:
        await _http.aclose()
        _http = None
    if _redis:
        await _redis.close()
        _redis = None

# -----------------------------------------------------------------------------
# Health / Live / Ready / Ops
# -----------------------------------------------------------------------------
@app.get("/live", response_class=PlainTextResponse, include_in_schema=True, tags=["ops"])
async def live():
    return PlainTextResponse("ok", status_code=200)

@app.get("/health", response_class=JSONResponse, include_in_schema=True, tags=["ops"])
async def health(deep: bool = Query(default=False)):
    out = platform_info()
    out.update({"status": "ok", "redis": False, "backend": "supabase" if _env_backend_ok() else "none", "backend_ok": _env_backend_ok()})

    # Redis
    r_ok = True
    r = await _get_redis()
    if r:
        try:
            pong = await r.ping()
            out["redis"] = bool(pong)
        except Exception as e:
            r_ok = False
            out["redis_error"] = str(e)

    if deep:
        b_ok = False
        if _env_backend_ok():
            try:
                _ = await supabase_rpc_get_graph(faccao_id=None, include_co=False, max_pairs=1)
                b_ok = True
            except Exception as e:
                out["backend_error"] = str(e)
        out["backend_reachable"] = b_ok
        out["ok"] = (not ENABLE_REDIS_CACHE or r_ok) and b_ok
    else:
        out["ok"] = (not ENABLE_REDIS_CACHE or r_ok) and _env_backend_ok()

    out["supabase"] = {
        "url": SUPABASE_URL,
        "rpc_fn": SUPABASE_RPC_FN,
        "timeout": SUPABASE_TIMEOUT,
        "service_key_tail": redact(SUPABASE_SERVICE_KEY),
    }
    return JSONResponse(out, status_code=200 if out.get("ok") else 503)

@app.get("/ready", response_class=JSONResponse, include_in_schema=True, tags=["ops"])
async def ready():
    r_ok = True
    out = platform_info()
    out.update({"redis": False, "backend": "supabase" if _env_backend_ok() else "none", "backend_ok": False})

    r = await _get_redis()
    if r:
        try:
            pong = await r.ping()
            out["redis"] = bool(pong)
        except Exception as e:
            r_ok = False
            out["redis_error"] = str(e)

    b_ok = False
    if _env_backend_ok():
        try:
            _ = await supabase_rpc_get_graph(faccao_id=None, include_co=False, max_pairs=1)
            b_ok = True
        except Exception as e:
            out["backend_error"] = str(e)
    out["backend_ok"] = b_ok

    ok = (not ENABLE_REDIS_CACHE or r_ok) and b_ok
    out["ok"] = ok
    return JSONResponse(out, status_code=200 if ok else 503)

@app.get("/ops/status", response_class=JSONResponse, tags=["ops"])
async def ops_status():
    info = platform_info()
    redis_cfg = {"enabled": ENABLE_REDIS_CACHE, "url": REDIS_URL}
    if ENABLE_REDIS_CACHE and aioredis:
        try:
            r = await _get_redis()
            if r:
                pong = await r.ping()
                redis_cfg["ping"] = bool(pong)
        except Exception as e:
            redis_cfg["error"] = str(e)

    supa = {
        "configured": _env_backend_ok(),
        "url": SUPABASE_URL,
        "rpc_fn": SUPABASE_RPC_FN,
        "timeout": SUPABASE_TIMEOUT,
        "service_key_tail": redact(SUPABASE_SERVICE_KEY),
    }
    info.update({
        "redis": redis_cfg,
        "supabase": supa,
        "env": {
            "CORS_ALLOW_ORIGINS": os.getenv("CORS_ALLOW_ORIGINS"),
            "CORS_ALLOW_METHODS": os.getenv("CORS_ALLOW_METHODS"),
            "CORS_ALLOW_HEADERS": os.getenv("CORS_ALLOW_HEADERS"),
        }
    })
    return JSONResponse(info, status_code=200)

# -----------------------------------------------------------------------------
# API: dados brutos
# -----------------------------------------------------------------------------
@app.get("/v1/graph/membros", response_class=JSONResponse, summary="Retorna grafo (nodes/edges)", tags=["graph"])
async def graph_membros(
    faccao_id: Optional[int] = Query(default=None),
    include_co: bool = Query(default=True),
    max_pairs: int = Query(default=8000, ge=1, le=200000),
    max_nodes: int = Query(default=2000, ge=100, le=20000),
    max_edges: int = Query(default=4000, ge=100, le=200000),
    cache: bool = Query(default=True)
):
    data = await fetch_graph_sanitized(faccao_id, include_co, max_pairs, use_cache=cache)
    data = truncate_preview(data, max_nodes, max_edges)
    return JSONResponse(data, status_code=200)

# -----------------------------------------------------------------------------
# VIS.JS (vis-network) – HTML simples; os dados vão embutidos e o JS em /static/vis-embed.js renderiza
# -----------------------------------------------------------------------------
@app.get("/v1/vis/visjs", response_class=HTMLResponse, tags=["viz"], summary="Visualização vis-network (dados embutidos)")
async def vis_visjs(
    response: Response,
    faccao_id: Optional[int] = Query(default=None),
    include_co: bool = Query(default=True),
    max_pairs: int = Query(default=8000, ge=1, le=200000),
    max_nodes: int = Query(default=2000, ge=100, le=20000),
    max_edges: int = Query(default=4000, ge=100, le=200000),
    cache: bool = Query(default=True),
    theme: str = Query(default="light", pattern="^(light|dark)$"),
    title: str = "Knowledge Graph (vis.js)",
    debug: bool = Query(default=False),
    source: str = Query(default="server", pattern="^(server|client)$"),
):
    # dados embutidos para evitar fetch extra
    data = await fetch_graph_sanitized(faccao_id, include_co, max_pairs, use_cache=cache)
    data = truncate_preview(data, max_nodes, max_edges)
    json_str = json.dumps(data, ensure_ascii=False)
    embedded_block = '<script id="__KG_DATA__" type="application/json">' + json_str + "</script>"

    local_js = "/static/vendor/vis-network.min.js"
    local_css = "/static/vendor/vis-network.min.css"
    bg = "#0b0f19" if theme == "dark" else "#ffffff"

    html = f"""
<!doctype html>
<html lang="pt-br">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <link rel="stylesheet" href="{local_css}">
    <link rel="stylesheet" href="/static/vis-style.css">
    <meta name="theme-color" content="{bg}">
    <style>html,body,#mynetwork{{height:100%;margin:0;}}</style>
  </head>
  <body data-theme="{theme}">
    <div class="kg-toolbar">
      <h4 style="margin:0">{title}</h4>
      <input id="kg-search" type="search" placeholder="Buscar nó por rótulo ou ID…" />
      <button id="btn-print" type="button" title="Imprimir">Print</button>
      <button id="btn-reload" type="button" title="Recarregar">Reload</button>
    </div>
    <div id="mynetwork" style="height:90vh;width:100%;" data-endpoint="/v1/graph/membros" data-source="server"></div>
    {embedded_block}
    <script src="{local_js}"></script>
    <script src="/static/vis-embed.js" defer></script>
  </body>
</html>
"""
    # CSP enxuta (assets locais)
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:;"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return HTMLResponse(content=html, status_code=200)

# -----------------------------------------------------------------------------
# PYVIS – geramos HTML com opções (arestas ultrafinas + busca e física off)
# -----------------------------------------------------------------------------
@app.get("/v1/vis/pyvis", response_class=HTMLResponse, tags=["viz"], summary="Visualização PyVis (arestas ultrafinas + busca)")
async def vis_pyvis(
    faccao_id: Optional[int] = Query(default=None),
    include_co: bool = Query(default=True),
    max_pairs: int = Query(default=8000, ge=1, le=200000),
    max_nodes: int = Query(default=2000, ge=100, le=20000),
    max_edges: int = Query(default=4000, ge=100, le=200000),
    cache: bool = Query(default=True),
    theme: str = Query(default="light", pattern="^(light|dark)$"),
    title: str = "Knowledge Graph (PyVis)",
    debug: bool = Query(default=False),
):
    try:
        data = await fetch_graph_sanitized(faccao_id, include_co, max_pairs, use_cache=cache)
        data = truncate_preview(data, max_nodes, max_edges)
        data = normalize_graph_labels(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"graph_fetch_error: {e}")

    nodes = data.get("nodes", []) or []
    edges = data.get("edges", []) or []
    if not nodes:
        return HTMLResponse("<h3>Sem dados para exibir (nodes=0)</h3>", status_code=200)

    faccao_name_by_id: Dict[str, str] = {}
    for n in nodes:
        if (n or {}).get("type") == "faccao" and n.get("id") is not None:
            fid = str(n["id"])
            faccao_name_by_id[fid] = str(n.get("label") or "").strip()

    def color_from_faccao(fid: Optional[str], n: Dict[str, Any]) -> Optional[str]:
        if (n.get("type") or "").lower() == "funcao" or str(n.get("group") or "") == "6":
            return "#c8a600"  # amarelo p/ função
        if not fid:
            return None
        name = (faccao_name_by_id.get(fid) or "").upper()
        if not name:
            return None
        if "PCC" in name:
            return "#0d47a1"
        if name == "CV" or "COMANDO VERMELHO" in name:
            return "#d32f2f"
        return None

    def hash_color(s: str) -> str:
        h = 0
        for ch in s:
            h = (h << 5) - h + ord(ch)
            h &= 0xFFFFFFFF
        hue = abs(h) % 360
        return f"hsl({hue},70%,50%)"

    height = "90vh"
    bgcolor = "#0b0f19" if theme == "dark" else "#ffffff"
    fontcolor = "#e8eaed" if theme == "dark" else "#111827"

    net = Network(
        height=height,
        width="100%",
        bgcolor=bgcolor,
        font_color=fontcolor,
        directed=True,
        cdn_resources="in_line",
    )

    seen = set()
    for n in nodes:
        if not n or n.get("id") is None:
            continue
        nid = str(n["id"])
        if nid in seen:
            continue
        seen.add(nid)

        label = str(n.get("label") or nid)
        group = str(n.get("group") or n.get("faccao_id") or n.get("type") or "0")
        size = n.get("size")
        photo = n.get("photo_url") if isinstance(n.get("photo_url"), str) and n["photo_url"].startswith(("http://", "https://")) else None

        fixed_color = color_from_faccao(group, n)
        color = fixed_color or hash_color(group)

        node_kwargs = dict(title=label, color=color, borderWidth=1)
        if isinstance(size, (int, float)):
            node_kwargs["value"] = float(size)

        if photo:
            node_kwargs["shape"] = "circularImage"
            node_kwargs["image"] = photo
        else:
            node_kwargs["shape"] = "dot"

        net.add_node(nid, label=label, **node_kwargs)

    valid_nodes = set(net.get_nodes())
    EDGE_COLORS = {
        "PERTENCE_A": "#9e9e9e",
        "EXERCE": "#00796b",
        "FUNCAO_DA_FACCAO": "#ef6c00",
        "CO_FACCAO": "#8e24aa",
        "CO_FUNCAO": "#546e7a",
    }
    for e in edges:
        if not e:
            continue
        a = str(e.get("source"))
        b = str(e.get("target"))
        if a in valid_nodes and b in valid_nodes:
            rel = e.get("relation") or ""
            try:
                w = float(e.get("weight") or 1.0)
            except Exception:
                w = 1.0
            color = EDGE_COLORS.get(rel, "#9e9e9e")
            # arestas ultrafinas e setas pequenas
            net.add_edge(a, b, value=w, width=0.2, color=color, title=f"{rel} (w={w})", arrows="to", smooth=False)

    # JSON (válido) de opções
    net.set_options(
        """
{
  "interaction": {
    "hover": true,
    "dragNodes": true,
    "dragView": false,
    "zoomView": true,
    "multiselect": true,
    "navigationButtons": true
  },
  "manipulation": { "enabled": false },
  "physics": {
    "enabled": true,
    "stabilization": { "enabled": true, "iterations": 300 },
    "barnesHut": {
      "gravitationalConstant": -8000,
      "centralGravity": 0.2,
      "springLength": 120,
      "springConstant": 0.04,
      "avoidOverlap": 0.2
    }
  },
  "layout": { "improvedLayout": true, "randomSeed": 42 },
  "nodes": { "shape": "dot", "borderWidth": 1 },
  "edges": {
    "smooth": false,
    "width": 0.2,
    "color": { "opacity": 0.65 },
    "arrows": { "to": { "enabled": true, "scaleFactor": 0.3 } }
  }
}
        """.strip()
    )

    html = net.generate_html()  # sem "title" (pyvis não aceita esse kw)

    # Toolbar + busca com destaque + desliga física após estabilização
    toolbar_css = """
    <style>
      .kg-toolbar { display:flex; gap:8px; align-items:center; padding:8px; border-bottom:1px solid #e0e0e0; }
      .kg-toolbar input[type="search"] { flex: 1; min-width: 220px; padding:6px 10px; }
      .kg-toolbar button { padding:6px 10px; border:1px solid #e0e0e0; background:transparent; border-radius:6px; cursor:pointer; }
      .kg-toolbar button:hover { background: rgba(0,0,0,.04); }
    </style>
    """
    toolbar_html = f"""
    <div class="kg-toolbar">
      <h4 style="margin:0">{title}</h4>
      <input id="kg-search" type="search" placeholder="Buscar nó por rótulo ou ID…" />
      <button id="btn-print" type="button" title="Imprimir">Print</button>
      <button id="btn-reload" type="button" title="Recarregar">Reload</button>
    </div>
    """
    toolbar_js = """
    <script>
      (function(){
        function colorObj(c, opacity){
          if (typeof c === 'object' && c) { return Object.assign({}, c, { opacity: opacity }); }
          return {
            background: c || '#90a4ae',
            border: c || '#90a4ae',
            highlight: { background: c || '#90a4ae', border: c || '#90a4ae' },
            hover: { background: c || '#90a4ae', border: c || '#90a4ae' },
            opacity: opacity
          };
        }
        function runSearch(txt){
          try{
            var ds = (typeof nodes !== 'undefined') ? nodes : (network && network.body && network.body.data && network.body.data.nodes);
            if (!ds) return;
            var all = ds.get();
            var t = (txt||'').trim().toLowerCase();
            if (!t){ return; }

            var hits = all.filter(function(n){ return (String(n.label||'').toLowerCase().includes(t)) || (String(n.id).toLowerCase()===t); });
            if (!hits.length) return;

            // esmaece todos
            all.forEach(function(n){ ds.update({ id: n.id, color: colorObj(n.color, 0.15), borderWidth: 1 }); });

            // destaca e foca no primeiro
            hits.forEach(function(h){ ds.update({ id: h.id, color: colorObj(h.color, 1), borderWidth: 4 }); });
            network.focus(hits[0].id, { scale: 1.2, animation: { duration: 400 } });
          }catch(e){ console.error(e); }
        }
        var q = document.getElementById('kg-search');
        var p = document.getElementById('btn-print');
        var r = document.getElementById('btn-reload');
        if (p) p.onclick = function(){ window.print(); };
        if (r) r.onclick = function(){ location.reload(); };
        if (q){
          q.addEventListener('keydown', function(e){ if(e.key==='Enter') runSearch(q.value); });
        }

        // para movimentos: desativa física depois de estabilizar
        if (typeof network !== 'undefined') {
          network.once('stabilizationIterationsDone', function(){
            network.fit({ animation: { duration: 300 } });
            network.setOptions({ physics: false });
          });
        }
      })();
    </script>
    """
    html = html.replace("</head>", toolbar_css + "\n</head>")
    html = html.replace("<body>", "<body>\n" + toolbar_html + "\n")
    html = html.replace("</body>", toolbar_js + "\n</body>")
    return HTMLResponse(content=html, status_code=200)
