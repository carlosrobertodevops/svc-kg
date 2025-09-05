# =============================================================================
# Arquivo: app.py
# Versão: v1.7.20
# Objetivo: API FastAPI do micro-serviço svc-kg (graph + visualizações + ops)
# Funções/métodos:
# - live/health/ready/ops_status: sondas e status operacional
# - graph_membros: retorna grafo (nós/arestas) via Supabase RPC
# - vis_visjs: página HTML com vis-network (busca, drag de nó, arestas finas, fotos)
# - vis_pyvis: página HTML com PyVis (arestas finas, repulsão forte, busca com destaque/pull, cores CV/PCC/funções)
# - Utilidades: normalização de labels de array PG, cache Redis, truncamento seguro
# =============================================================================

import os
import json
import asyncio
import logging
import socket
from typing import Optional, Dict, Any

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

# Backend: Supabase
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
# FastAPI
# -----------------------------------------------------------------------------
app = FastAPI(
    title="svc-kg",
    version="v1.7.20",
    description="Micro serviço de Knowledge Graph com visualizações (vis.js e PyVis).",
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
)

# Static
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
if os.path.isdir("docs"):
    app.mount("/docs-static", StaticFiles(directory="docs"), name="docs-static")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=(
        [o.strip() for o in CORS_ALLOW_ORIGINS.split(",")]
        if CORS_ALLOW_ORIGINS != "*"
        else ["*"]
    ),
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


def truncate_preview(
    data: Dict[str, Any], max_nodes: int, max_edges: int
) -> Dict[str, Any]:
    ns = data.get("nodes", [])[: max(0, max_nodes)]
    idset = {str(n["id"]) for n in ns if n and "id" in n}
    es = [
        e
        for e in (data.get("edges", []) or [])
        if e and str(e.get("source")) in idset and str(e.get("target")) in idset
    ]
    es = es[: max(0, max_edges)]
    return {"nodes": ns, "edges": es}


# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
# RPC com FALLBACK de assinatura (p_* primeiro; depois plain)
# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
async def supabase_rpc_get_graph(
    faccao_id: Optional[int], include_co: bool, max_pairs: int
) -> Dict[str, Any]:
    """
    Chama o RPC no PostgREST tentando assinaturas conhecidas:

    1) get_graph_membros(p_faccao_id, p_include_co, p_max_pairs)
    2) get_graph_membros(faccao_id, include_co, max_pairs)

    Retorna dicionário {"nodes": [...], "edges": [...]} ou lança RuntimeError.
    """
    if not _env_backend_ok():
        raise RuntimeError(
            "backend_not_configured: defina SUPABASE_URL e SUPABASE_SERVICE_KEY"
        )

    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/rpc/{SUPABASE_RPC_FN}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    attempts = [
        {
            "p_faccao_id": faccao_id,
            "p_include_co": include_co,
            "p_max_pairs": max_pairs,
        },  # assinatura mais comum no seu Supabase
        {
            "faccao_id": faccao_id,
            "include_co": include_co,
            "max_pairs": max_pairs,
        },  # fallback
    ]

    last_status = None
    last_body = None
    client = await _get_http()

    for idx, payload in enumerate(attempts, start=1):
        resp = await client.post(url, json=payload, headers=headers)
        last_status, last_body = resp.status_code, resp.text
        if resp.status_code == 200:
            data = resp.json()
            if not isinstance(data, dict):
                if (
                    isinstance(data, list)
                    and data
                    and isinstance(data[0], dict)
                    and "nodes" in data[0]
                ):
                    data = data[0]
                else:
                    raise RuntimeError(
                        "Formato inesperado do RPC (esperado objeto com nodes/edges)"
                    )
            # sucesso
            if idx > 1:
                log.info(
                    "RPC %s respondeu após fallback de assinatura (%s)",
                    SUPABASE_RPC_FN,
                    list(payload.keys()),
                )
            return data

        # Se 404 + PGRST202, tenta a próxima forma. Se outro erro, interrompe.
        if resp.status_code != 404 or "PGRST202" not in resp.text:
            break

    raise RuntimeError(
        f"{last_status}: Supabase RPC {SUPABASE_RPC_FN} falhou: {last_body}"
    )


# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<


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
            cnt = fh.read()
            return "docker" in cnt or "kubepods" in cnt
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
    log.info(
        "svc-kg iniciado (backend: %s, cache: %s)",
        "supabase" if _env_backend_ok() else "none",
        "redis" if ENABLE_REDIS_CACHE else "none",
    )


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
# Ops
# -----------------------------------------------------------------------------
@app.get(
    "/live", response_class=PlainTextResponse, include_in_schema=True, tags=["ops"]
)
async def live():
    return PlainTextResponse("ok", status_code=200)


@app.get("/health", response_class=JSONResponse, include_in_schema=True, tags=["ops"])
async def health(deep: bool = Query(default=False)):
    out = platform_info()
    out.update(
        {
            "status": "ok",
            "redis": False,
            "backend": "supabase" if _env_backend_ok() else "none",
            "backend_ok": _env_backend_ok(),
        }
    )

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
                _ = await supabase_rpc_get_graph(
                    faccao_id=None, include_co=False, max_pairs=1
                )
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
    out.update(
        {
            "redis": False,
            "backend": "supabase" if _env_backend_ok() else "none",
            "backend_ok": False,
        }
    )

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
            _ = await supabase_rpc_get_graph(
                faccao_id=None, include_co=False, max_pairs=1
            )
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

    info.update(
        {
            "redis": redis_cfg,
            "supabase": supa,
            "env": {
                "CORS_ALLOW_ORIGINS": os.getenv("CORS_ALLOW_ORIGINS"),
                "CORS_ALLOW_METHODS": os.getenv("CORS_ALLOW_METHODS"),
                "CORS_ALLOW_HEADERS": os.getenv("CORS_ALLOW_HEADERS"),
            },
        }
    )
    return JSONResponse(info, status_code=200)


# -----------------------------------------------------------------------------
# Graph data
# -----------------------------------------------------------------------------
@app.get(
    "/v1/graph/membros",
    response_class=JSONResponse,
    summary="Retorna grafo (nodes/edges)",
    tags=["graph"],
)
async def graph_membros(
    faccao_id: Optional[int] = Query(default=None),
    include_co: bool = Query(default=True),
    max_pairs: int = Query(default=8000, ge=1, le=200000),
    max_nodes: int = Query(default=2000, ge=100, le=20000),
    max_edges: int = Query(default=4000, ge=100, le=200000),
    cache: bool = Query(default=True),
):
    data = await fetch_graph_sanitized(
        faccao_id, include_co, max_pairs, use_cache=cache
    )
    data = truncate_preview(data, max_nodes, max_edges)
    return JSONResponse(data, status_code=200)


# -----------------------------------------------------------------------------
# vis.js
# -----------------------------------------------------------------------------
@app.get(
    "/v1/vis/visjs",
    response_class=HTMLResponse,
    tags=["viz"],
    summary="Visualização vis-network",
)
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
    local_js = "static/vendor/vis-network.min.js"
    local_css = "static/vendor/vis-network.min.css"
    has_local = os.path.exists(local_js) and os.path.exists(local_css)
    if has_local:
        js_href = "/static/vendor/vis-network.min.js"
        css_href = "/static/vendor/vis-network.min.css"
        csp = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self';"
    else:
        js_href = "https://unpkg.com/vis-network@9.1.6/dist/vis-network.min.js"
        css_href = "https://unpkg.com/vis-network@9.1.6/styles/vis-network.min.css"
        csp = "default-src 'self'; style-src 'self' 'unsafe-inline' https://unpkg.com; script-src 'self' 'unsafe-inline' https://unpkg.com; img-src 'self' data:; font-src 'self' data:; connect-src 'self';"

    embedded_block = ""
    if source == "server":
        data = await fetch_graph_sanitized(
            faccao_id, include_co, max_pairs, use_cache=cache
        )
        out = truncate_preview(data, max_nodes, max_edges)
        out = normalize_graph_labels(out)
        json_str = json.dumps(out, ensure_ascii=False)
        embedded_block = (
            '<script id="__KG_DATA__" type="application/json">' + json_str + "</script>"
        )

    bg = "#0b0f19" if theme == "dark" else "#ffffff"

    html = """
<!doctype html>
<html lang="pt-br">
  <head>
    <meta charset="utf-8" />
    <meta http-equiv="x-ua-compatible" content="ie=edge" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Knowledge Graph (vis.js)</title>
    <link rel="stylesheet" href="{css_href}">
    <link rel="stylesheet" href="/static/vis-style.css">
    <meta name="theme-color" content="{bg}">
    <style>html,body,#mynetwork {{ height:100%; margin:0; }}</style>
  </head>
  <body data-theme="{theme}">
    <div class="kg-toolbar">
      <h4 style="margin:0">Knowledge Graph (vis.js)</h4>
      <input id="kg-search" type="search" placeholder="Buscar nó por rótulo ou ID…" />
      <button id="btn-print" type="button" title="Imprimir">Print</button>
      <button id="btn-reload" type="button" title="Recarregar">Reload</button>
      <span id="badge" class="badge" style="display:{debug_display}">debug</span>
    </div>

    <div id="mynetwork" style="height:90vh;width:100%;" data-endpoint="/v1/graph/membros" data-debug="{debug_bool}" data-source="{source}"></div>

    {embedded_block}
    <script src="{js_href}" crossorigin="anonymous"></script>
    <script src="/static/vis-embed.js" defer></script>
  </body>
</html>
""".format(
        css_href=css_href,
        bg=bg,
        theme=theme,
        debug_display="inline-block" if debug else "none",
        debug_bool=str(debug).lower(),
        source=source,
        js_href=js_href,
        embedded_block=embedded_block,
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self';"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return HTMLResponse(content=html, status_code=200)


# -----------------------------------------------------------------------------
# PyVis (arestas finas, repulsão forte, busca com destaque/pull, cores)
# -----------------------------------------------------------------------------
@app.get(
    "/v1/vis/pyvis",
    response_class=HTMLResponse,
    tags=["viz"],
    summary="Visualização com PyVis",
)
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
        data = await fetch_graph_sanitized(
            faccao_id, include_co, max_pairs, use_cache=cache
        )
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

    def color_from_faccao(fid: Optional[str]) -> Optional[str]:
        if not fid:
            return None
        name = (faccao_name_by_id.get(fid) or "").upper()
        if not name:
            return None
        if "PCC" in name:
            return "#0d47a1"  # azul escuro
        if name == "CV" or "COMANDO VERMELHO" in name:
            return "#d32f2f"  # vermelho
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

    # Nós
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
        photo = (
            n.get("photo_url")
            if isinstance(n.get("photo_url"), str)
            and n["photo_url"].startswith(("http://", "https://"))
            else None
        )

        fixed_color = color_from_faccao(group)
        if n.get("type") == "funcao":
            fixed_color = "#fdd835"  # amarelo função

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

    # Arestas (finas; funções amarelas)
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
            edge_color = "#bdbdbd"
            if rel in ("EXERCE", "FUNCAO_DA_FACCAO"):
                edge_color = "#fdd835"
            net.add_edge(
                a, b, value=w, width=1, color=edge_color, title=f"{rel} (w={w})"
            )

    # Tamanho por grau se necessário
    if not any(isinstance(n.get("value"), (int, float)) for n in net.nodes):
        deg = {nid: 0 for nid in valid_nodes}
        for e in net.edges:
            deg[e["from"]] = deg.get(e["from"], 0) + 1
            deg[e["to"]] = deg.get(e["to"], 0) + 1
        for nid, d in deg.items():
            val = 8 + (d**0.65) * 4
            net.get_node(nid)["value"] = val

    # Opções JSON (repulsão forte + arestas finas)
    net.set_options(
        """
    {
      "interaction": {
        "hover": true,
        "dragNodes": true,
        "dragView": true,
        "zoomView": true,
        "navigationButtons": true
      },
      "physics": {
        "enabled": true,
        "stabilization": { "enabled": true, "iterations": 160 },
        "repulsion": {
          "centralGravity": 0.15,
          "springLength": 120,
          "springConstant": 0.025,
          "nodeDistance": 190,
          "damping": 0.09
        }
      },
      "nodes": { "shape": "dot", "borderWidth": 1 },
      "edges": {
        "smooth": false,
        "width": 1,
        "color": { "color": "#bdbdbd", "highlight": "#424242", "opacity": 0.55 }
      }
    }
    """
    )

    html = net.generate_html()

    # Toolbar + busca destacando e “puxando” o nó
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
        function runAfterNetworkReady(cb){
          if (typeof network !== 'undefined') cb();
          else setTimeout(()=>runAfterNetworkReady(cb), 80);
        }
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
        function doSearch(){
          var q = document.getElementById('kg-search').value.trim().toLowerCase();
          if (!q) return;
          var ds = (typeof nodes !== 'undefined') ? nodes : (network && network.body && network.body.data && network.body.data.nodes);
          if (!ds) return;
          var all = ds.get();
          var hit = all.find(function(n){ return (String(n.label||'').toLowerCase().includes(q)) || (String(n.id)===q); });
          if (!hit) { alert('Nenhum nó encontrado.'); return; }

          all.forEach(function(n){ ds.update({ id: n.id, color: colorObj(n.color, 0.35), borderWidth: 1, shadow: false, font: { size: 12 } }); });

          var cur = ds.get(hit.id);
          ds.update({ id: hit.id, color: colorObj(cur.color, 1), borderWidth: 3, shadow: { enabled: true, size: 18, x:2, y:2 }, font: { size: 16 } });

          var pos = network.getPositions([hit.id])[hit.id] || {x:0,y:0};
          ds.update({ id: hit.id, x: pos.x + 150, y: pos.y - 70, fixed: {x:false, y:false} });
          network.focus(hit.id, { scale: 1.2, animation: { duration: 500, easingFunction: 'easeInOutQuad' } });
        }

        runAfterNetworkReady(function(){
          network.once('stabilizationIterationsDone', function(){ network.setOptions({ physics: { enabled: false } }); });
          var p = document.getElementById('btn-print');
          var r = document.getElementById('btn-reload');
          var s = document.getElementById('kg-search');
          if (p) p.onclick = function(){ window.print(); };
          if (r) r.onclick = function(){ location.reload(); };
          if (s) s.addEventListener('keydown', function(e){ if (e.key === 'Enter') doSearch(); });
        });
      })();
    </script>
    """
    html = html.replace("</head>", toolbar_css + "\n</head>")
    html = html.replace("<body>", "<body>\n" + toolbar_html + "\n")
    html = html.replace("</body>", toolbar_js + "\n</body>")
    return HTMLResponse(content=html, status_code=200)


# -----------------------------------------------------------------------------
# /docs (Swagger custom)
# -----------------------------------------------------------------------------
@app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
async def custom_docs():
    csp = (
        "default-src 'self'; "
        "img-src 'self' data: https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "connect-src 'self';"
    )
    html = r"""
<!doctype html>
<html lang="pt-br">
  <head>
    <meta charset="utf-8"/>
    <title>svc-kg • API Docs</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist/swagger-ui.css">
    <style>
      body { margin:0; }
      .ops-bar { padding: 12px 16px; border-bottom: 1px solid #eee; background:#fafafa; display: grid; grid-template-columns: 1fr auto; gap: 12px; align-items: center; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; }
      .ops-right { display:flex; gap:8px; align-items:center; }
      .ops-pill { padding:4px 8px; border-radius:999px; font-size:12px; border:1px solid #e0e0e0; background:#fff; }
      .ops-pill.ok { border-color:#c8e6c9; background:#e8f5e9; }
      .ops-pill.err{ border-color:#ffcdd2; background:#ffebee; }
      .ops-grid { display:grid; grid-template-columns: repeat(4, minmax(160px,1fr)); gap:8px; margin-top: 6px; }
      .ops-kv { font-size: 12px; color:#333; background:#fff; border:1px solid #eee; border-radius:8px; padding:8px; }
      .ops-kv b { color:#111; }
      #swagger-ui { margin-top: 0; }
      .note { font-size: 12px; color:#666; }
      @media (max-width: 900px){ .ops-grid { grid-template-columns: repeat(2, minmax(160px,1fr)); } }
    </style>
  </head>
  <body>
    <div class="ops-bar">
      <div>
        <div style="font-weight:600;">svc-kg (a.k.a. “sic-kg”)</div>
        <div class="ops-grid" id="ops-kvs">
          <div class="ops-kv"><b>Version</b><div id="kv-version">—</div></div>
          <div class="ops-kv"><b>Env</b><div id="kv-env">—</div></div>
          <div class="ops-kv"><b>Platform</b><div id="kv-platform">—</div></div>
          <div class="ops-kv"><b>Host</b><div id="kv-host">—</div></div>
          <div class="ops-kv"><b>Redis</b><div id="kv-redis">—</div></div>
          <div class="ops-kv"><b>Supabase URL</b><div id="kv-supa">—</div></div>
          <div class="ops-kv"><b>RPC</b><div id="kv-rpc">—</div></div>
          <div class="ops-kv"><b>Timeout</b><div id="kv-timeout">—</div></div>
        </div>
        <div class="note">Use os botões para testar live/health/ready. O painel atualiza automaticamente ao abrir.</div>
      </div>
      <div class="ops-right">
        <a class="ops-pill" href="/live" target="_blank">/live</a>
        <a class="ops-pill" href="/health" target="_blank">/health</a>
        <a class="ops-pill" href="/health?deep=true" target="_blank">/health?deep=true</a>
        <a class="ops-pill" href="/ready" target="_blank">/ready</a>
        <a class="ops-pill" href="/ops/status" target="_blank">/ops/status</a>
      </div>
    </div>

    <div id="swagger-ui"></div>

    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist/swagger-ui-bundle.js"></script>
    <script>
      function setKV(id, val){ var el=document.getElementById(id); if(el) el.textContent = (val==null?'—': String(val)); }
      async function j(u){ try{ const r=await fetch(u); return await r.json(); }catch(e){ return {error:String(e)}; } }

      async function refresh(){
        const [ops, health] = await Promise.all([ j('/ops/status'), j('/health?deep=true') ]);
        setKV('kv-version', ops.version || '—');
        setKV('kv-env', ops.app_env || '—');
        setKV('kv-platform', ops.coolify_proxy_network ? 'coolify' : (ops.container ? 'container' : 'host'));
        setKV('kv-host', ops.hostname || location.hostname);
        setKV('kv-redis', (ops.redis && ops.redis.enabled) ? (ops.redis.ping ? 'ok' : 'enabled') : 'disabled');
        setKV('kv-supa', (ops.supabase && ops.supabase.url) ? ops.supabase.url : '—');
        setKV('kv-rpc', (ops.supabase && ops.supabase.rpc_fn) ? ops.supabase.rpc_fn : '—');
        setKV('kv-timeout', (ops.supabase && ops.supabase.timeout) ? ops.supabase.timeout : '—');
      }

      window.onload = function(){
        SwaggerUIBundle({ url: '/openapi.json', dom_id: '#swagger-ui', deepLinking: true, docExpansion: 'none', defaultModelsExpandDepth: -1 });
        refresh();
      };
    </script>
  </body>
</html>
"""
    resp = HTMLResponse(content=html, status_code=200)
    resp.headers["Content-Security-Policy"] = csp
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
