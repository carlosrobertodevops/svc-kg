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
    from redis import asyncio as aioredis
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

# Fotos (tabela membros)
MEMBERS_TABLE = os.getenv("MEMBERS_TABLE", "membros")
MEMBERS_ID_COL = os.getenv("MEMBERS_ID_COL", "id")
MEMBERS_PHOTO_COL = os.getenv("MEMBERS_PHOTO_COL", "photo_url")  # ex.: "foto_path"
MEMBERS_PHOTO_BASE_URL = os.getenv("MEMBERS_PHOTO_BASE_URL", "").strip()

# Metadata
SERVICE_ID = "svc-kg"
SERVICE_AKA = ["sic-kg"]

# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------
app = FastAPI(
    title="svc-kg",
    version="v1.7.15-photos-docs",
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
if os.path.isdir("assets"):
    app.mount("/assets", StaticFiles(directory="assets"), name="assets")

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
        "faccao_id": faccao_id,
        "p_faccao_id": faccao_id,
        "include_co": include_co,
        "p_include_co": include_co,
        "max_pairs": max_pairs,
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

# ------------------------- Fotos: resolução e enriquecimento ------------------
def resolve_photo_url(value: Optional[str]) -> Optional[str]:
    """Monta URL final da foto."""
    if not value:
        return None
    s = str(value).strip()
    if s.startswith(("http://", "https://", "data:")):
        return s
    if s.startswith("/assets/"):
        return s
    if MEMBERS_PHOTO_BASE_URL:
        return MEMBERS_PHOTO_BASE_URL.rstrip("/") + "/" + s.lstrip("/")
    return "/assets/" + s.lstrip("/")

def map_photo_field_on_nodes(nodes: List[Dict[str, Any]]):
    src = MEMBERS_PHOTO_COL or "photo_url"
    for n in nodes:
        v = n.get("photo_url") or n.get(src) or n.get("foto_path")
        url = resolve_photo_url(v) if v else None
        if url:
            n["photo_url"] = url

async def enrich_nodes_with_member_photos(nodes: List[Dict[str, Any]]):
    if not _env_backend_ok() or not nodes:
        return
    missing_ids = [str(n["id"]) for n in nodes if n.get("id") is not None and not n.get("photo_url")]
    if not missing_ids:
        return

    base = SUPABASE_URL.rstrip("/")
    table = MEMBERS_TABLE
    idcol = MEMBERS_ID_COL
    pcol = MEMBERS_PHOTO_COL or "photo_url"

    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Accept": "application/json",
    }
    client = await _get_http()

    batch = 100
    found: Dict[str, str] = {}
    for i in range(0, len(missing_ids), batch):
        chunk = missing_ids[i:i+batch]
        in_list = ",".join(chunk)  # ids numéricos; adapte se for string
        url = f"{base}/rest/v1/{table}?select={idcol},{pcol}&{idcol}=in.({in_list})"
        r = await client.get(url, headers=headers)
        if r.status_code != 200:
            log.warning("enrich photos failed %s: %s", r.status_code, r.text)
            continue
        rows = r.json()
        for row in rows:
            rid = str(row.get(idcol))
            path = row.get(pcol)
            if rid and path:
                final_url = resolve_photo_url(str(path))
                if final_url:
                    found[rid] = final_url

    for n in nodes:
        nid = str(n.get("id"))
        if nid and not n.get("photo_url") and nid in found:
            n["photo_url"] = found[nid]

async def fetch_graph_sanitized(faccao_id: Optional[int], include_co: bool, max_pairs: int, use_cache: bool = True, enrich_photos: bool = True) -> Dict[str, Any]:
    cache_key = _cache_key("graph", {"faccao_id": faccao_id, "include_co": include_co, "max_pairs": max_pairs})
    if use_cache:
        r = await _get_redis()
        if r:
            cached = await r.get(cache_key)
            if cached:
                try:
                    data = json.loads(cached)
                    return data
                except Exception:
                    pass

    raw = await supabase_rpc_get_graph(faccao_id, include_co, max_pairs)
    fixed = normalize_graph_labels(raw)

    nodes = fixed.get("nodes", []) or []
    map_photo_field_on_nodes(nodes)
    if enrich_photos:
        try:
            await enrich_nodes_with_member_photos(nodes)
        except Exception as e:
            log.warning("photo enrichment error: %s", e)

    if use_cache:
        r = await _get_redis()
        if r:
            await r.set(cache_key, json.dumps(fixed, ensure_ascii=False), ex=CACHE_API_TTL)
    return fixed

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
    cache: bool = Query(default=True),
    enrich_photos: bool = Query(default=True, description="Se true, tenta preencher foto consultando a tabela membros")
):
    data = await fetch_graph_sanitized(faccao_id, include_co, max_pairs, use_cache=cache, enrich_photos=enrich_photos)
    data = truncate_preview(data, max_nodes, max_edges)
    return JSONResponse(data, status_code=200)

# -----------------------------------------------------------------------------
# VIS.JS
# -----------------------------------------------------------------------------
@app.get(
    "/v1/vis/visjs",
    response_class=HTMLResponse,
    summary="Visualização vis-network (fotos circulares, fallback ícone, cores CV/PCC, arestas finas, drag isolado, busca)",
    tags=["viz"]
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
    enrich_photos: bool = Query(default=True),
):
    local_js = "static/vendor/vis-network.min.js"
    local_css = "static/vendor/vis-network.min.css"
    has_local = os.path.exists(local_js) and os.path.exists(local_css)
    if has_local:
        js_href = "/static/vendor/vis-network.min.js"
        css_href = "/static/vendor/vis-network.min.css"
        csp = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "font-src 'self' data:; connect-src 'self';"
        )
    else:
        js_href = "https://unpkg.com/vis-network@9.1.6/dist/vis-network.min.js"
        css_href = "https://unpkg.com/vis-network@9.1.6/styles/vis-network.min.css"
        csp = (
            "default-src 'self'; style-src 'self' 'unsafe-inline' https://unpkg.com; "
            "script-src 'self' 'unsafe-inline' https://unpkg.com; "
            "img-src 'self' data:; font-src 'self' data:; connect-src 'self';"
        )

    embedded_block = ""
    if source == "server":
        data = await fetch_graph_sanitized(faccao_id, include_co, max_pairs, use_cache=cache, enrich_photos=enrich_photos)
        out = truncate_preview(data, max_nodes, max_edges)
        out = normalize_graph_labels(out)
        json_str = json.dumps(out, ensure_ascii=False)
        embedded_block = '<script id="__KG_DATA__" type="application/json">' + json_str + "</script>"

    bg = "#0b0f19" if theme == "dark" else "#ffffff"

    html = """
<!doctype html>
<html lang="pt-br">
  <head>
    <meta charset="utf-8" />
    <meta http-equiv="x-ua-compatible" content="ie=edge" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <link rel="stylesheet" href="{css_href}">
    <link rel="stylesheet" href="/static/vis-style.css">
    <meta name="theme-color" content="{bg}">
    <style>
      html,body,#mynetwork {{ height:100%; margin:0; }}
      .kg-toolbar {{ display:flex; gap:8px; align-items:center; padding:8px; border-bottom:1px solid #e0e0e0; }}
      .kg-toolbar input[type="search"] {{ flex: 1; min-width: 220px; padding:6px 10px; }}
      .badge {{ background:#eee; border-radius:6px; padding:2px 8px; font-size:12px; }}
    </style>
  </head>
  <body data-theme="{theme}">
    <div class="kg-toolbar">
      <h4 style="margin:0">{title}</h4>
      <input id="kg-search" type="search" placeholder="Buscar nó por rótulo ou ID…" />
      <button id="btn-print" type="button" title="Imprimir">Print</button>
      <button id="btn-reload" type="button" title="Recarregar">Reload</button>
      <span id="badge" class="badge" style="display:{debug_display}">debug</span>
    </div>

    <div id="mynetwork"
         style="height:90vh;width:100%;"
         data-endpoint="/v1/graph/membros"
         data-debug="{debug_bool}"
         data-source="{source}"></div>

    {embedded_block}
    <script src="{js_href}" crossorigin="anonymous"></script>

    <script>
    (function(){{
      const COLOR_CV  = '#d32f2f';
      const COLOR_PCC = '#0d47a1';
      const EDGE_COLORS = {{
        'PERTENCE_A':      '#9e9e9e',
        'EXERCE':          '#00796b',
        'FUNCAO_DA_FACCAO':'#ef6c00',
        'CO_FACCAO':       '#8e24aa',
        'CO_FUNCAO':       '#546e7a'
      }};
      const ICON_PERSON = '/static/icons/person.svg';

      const container = document.getElementById('mynetwork');
      const source = container.getAttribute('data-source') || 'server';
      const debug = container.getAttribute('data-debug') === 'true';
      const endpoint = container.getAttribute('data-endpoint') || '/v1/graph/membros';

      const q = document.getElementById('kg-search');
      const btnPrint = document.getElementById('btn-print');
      const btnReload = document.getElementById('btn-reload');
      const badge = document.getElementById('badge');

      function hashColor(s) {{
        s = String(s||''); let h=0; for (let i=0;i<s.length;i++) {{ h=(h<<5)-h+s.charCodeAt(i); h|=0; }}
        const hue=Math.abs(h)%360; return `hsl(${{hue}},70%,50%)`;
      }}
      function isPgTextArray(s) {{ s=(s||'').trim(); return s.length>=2 && s[0]=='{{' && s[s.length-1]=='}'; }}
      function cleanLabel(raw) {{
        if(!raw) return '';
        const s=String(raw).trim();
        if(!isPgTextArray(s)) return s;
        const inner=s.slice(1,-1);
        if(!inner) return '';
        return inner.replace(/(^|,)\\s*"?null"?\\s*(?=,|$)/gi,'')
                    .replace(/"/g,'')
                    .split(',').map(x=>x.trim()).filter(Boolean).join(', ');
      }}
      function degreeMap(nodes,edges) {{
        const d={{}}; nodes.forEach(n=>d[n.id]=0);
        edges.forEach(e=>{{ if(e.from in d) d[e.from]++; if(e.to in d) d[e.to]++; }});
        return d;
      }}
      function inferFaccaoColors(rawNodes) {{
        const map={{}};
        rawNodes.filter(n=>n && n.type==='faccao').forEach(n=>{{
          const name = cleanLabel(n.label||'').toUpperCase();
          const id = String(n.id);
          if(!name) return;
          if (name.includes('PCC')) map[id] = COLOR_PCC;
          else if (name === 'CV' || name.includes('COMANDO VERMELHO')) map[id] = COLOR_CV;
        }});
        return map;
      }}
      function colorForNode(n, faccaoColorById) {{
        const gid = String(n.group ?? n.faccao_id ?? '');
        if (gid && faccaoColorById[gid]) return faccaoColorById[gid];
        return hashColor(gid || (n.type||'x'));
      }}
      function edgeStyleFor(relation) {{
        const base = EDGE_COLORS[relation] || '#90a4ae';
        return {{ color: base }};
      }}
      function attachToolbar(net,nc,ec,dsNodes) {{
        if (btnPrint) btnPrint.onclick = ()=>window.print();
        if (btnReload) btnReload.onclick = ()=>location.reload();
        if (badge && debug) badge.textContent = `nodes: ${{nc}} · edges: ${{ec}}`;
        if (q) {{
          q.addEventListener('change', ()=>selectByQuery(net, dsNodes, q.value));
          q.addEventListener('keyup', (e)=>{{ if(e.key==='Enter') selectByQuery(net, dsNodes, q.value); }});
        }}
      }}
      function selectByQuery(net, dsNodes, query) {{
        const text = (query||'').trim().toLowerCase();
        if (!text) return;
        const all = dsNodes.get();
        const hits = all.filter(n => (n.label||'').toLowerCase().includes(text) || String(n.id)===text);
        if (!hits.length) return;
        dsNodes.update(all.map(n => Object.assign(n, {{ color: Object.assign({{}}, n.color, {{ opacity: 0.25 }}) }})));
        const ids = hits.map(h=>h.id);
        ids.forEach(id => {{
          const cur = dsNodes.get(id);
          const hi = Object.assign({{}}, cur.color || {{}}, {{ opacity: 1 }});
          dsNodes.update({{ id, color: hi }});
        }});
        net.fit({{ nodes: ids, animation: {{ duration: 300 }} }});
      }}

      function isPersonType(t) {{
        t = String(t||'').toLowerCase();
        return t.includes('pessoa') || t.includes('membro');
      }}

      function render(data) {{
        const rawNodes = data.nodes || [];
        const rawEdges = data.edges || [];
        const faccaoColorById = inferFaccaoColors(rawNodes);

        const nodes = rawNodes
          .filter(n=>n&&n.id!=null)
          .map(n=>{{
            const id = String(n.id);
            const label = cleanLabel(n.label)||id;
            const group = String(n.group ?? n.faccao_id ?? n.type ?? '0');
            const value = (typeof n.size==='number') ? n.size : undefined;
            const photo = n.photo_url && /^https?:\\/\\//i.test(n.photo_url) ? n.photo_url
                        : (n.photo_url && n.photo_url.startsWith('/assets/')) ? n.photo_url
                        : null;
            const color = colorForNode({{group, type:n.type}}, faccaoColorById);
            const base = {{ id, label, group, value, color, borderWidth: 1 }};
            if (photo) {{ base.shape = 'circularImage'; base.image = photo; }}
            else if (isPersonType(n.type)) {{ base.shape = 'image'; base.image = ICON_PERSON; }}
            else {{ base.shape = 'dot'; }}
            return base;
          }});

        const nodeIds = new Set(nodes.map(n=>n.id));
        const edges = rawEdges
          .filter(e=>e&&e.source!=null&&e.target!=null && nodeIds.has(String(e.source)) && nodeIds.has(String(e.target)))
          .map(e=>{{
            const rel = e.relation || '';
            const style = edgeStyleFor(rel);
            return {{ from:String(e.source), to:String(e.target), value:(e.weight!=null? Number(e.weight):1.0), title: rel ? `${{rel}} (w=${{e.weight ?? 1}})` : `w=${{e.weight ?? 1}}`, width:1, color: style }};
          }});

        if (!nodes.length) {{
          container.innerHTML='<div style="display:flex;height:100%;align-items:center;justify-content:center;opacity:.85">Nenhum dado para exibir (nodes=0).</div>';
          return;
        }}

        const hasSize = nodes.some(n=>typeof n.value==='number');
        if(!hasSize) {{
          const deg=degreeMap(nodes,edges);
          nodes.forEach(n=>{{ const d=deg[n.id]||0; n.value=10+Math.log(d+1)*8; }});
        }}

        const dsNodes = new vis.DataSet(nodes);
        const dsEdges = new vis.DataSet(edges);

        const options = {{
          interaction: {{ hover: true, dragNodes: true, dragView: false, zoomView: true, multiselect: true, navigationButtons: true }},
          manipulation: {{ enabled: false }},
          physics: {{
            enabled: true,
            stabilization: {{ enabled: true, iterations: 300 }},
            barnesHut: {{ gravitationalConstant: -8000, centralGravity: 0.2, springLength: 120, springConstant: 0.04, avoidOverlap: 0.2 }}
          }},
          nodes: {{ shape: 'dot', borderWidth: 1 }},
          edges: {{ smooth: false }}
        }};

        const net = new vis.Network(container, {{nodes: dsNodes, edges: dsEdges}}, options);

        // Após estabilizar, desliga a física: ao arrastar, só o nó move (não puxa o grafo todo)
        net.once('stabilizationIterationsDone',()=>{{ net.setOptions({{ physics: false }}); net.fit({{animation:{{duration:300}}}}); }});
        net.on('doubleClick',()=>net.fit({{animation:{{duration:300}}}}));

        attachToolbar(net, nodes.length, edges.length, dsNodes);
      }}

      function run() {{
        if(typeof vis==='undefined') {{
          container.innerHTML='<div style="padding:12px">vis-network não carregou. Verifique CSP/CDN.</div>';
          return;
        }}
        if(source==='server') {{
          const tag=document.getElementById('__KG_DATA__');
          if(!tag) {{ container.innerHTML='<div style="padding:12px">Bloco de dados ausente.</div>'; return; }}
          try {{ render(JSON.parse(tag.textContent||'{{}}')); }}
          catch(e){{ console.error(e); container.innerHTML='<pre>'+String(e)+'</pre>'; }}
        }} else {{
          const params=new URLSearchParams(window.location.search);
          const qs=new URLSearchParams();
          const fac=params.get('faccao_id'); if(fac && fac.trim()!=='') qs.set('faccao_id',fac.trim());
          qs.set('include_co', params.get('include_co') ?? 'true');
          qs.set('max_pairs',  params.get('max_pairs')  ?? '8000');
          qs.set('max_nodes',  params.get('max_nodes')  ?? '2000');
          qs.set('max_edges',  params.get('max_edges')  ?? '4000');
          qs.set('cache',      params.get('cache')      ?? 'false');
          qs.set('enrich_photos', params.get('enrich_photos') ?? 'true');
          const url=endpoint+'?'+qs.toString();
          fetch(url,{{headers:{{'Accept':'application/json'}}}})
            .then(async r=>{{ if(!r.ok) throw new Error(r.status+': '+await r.text()); return r.json(); }})
            .then(render)
            .catch(err=>{{ console.error(err); container.innerHTML='<pre>'+String(err).replace(/</g,'&lt;')+'</pre>'; }});
        }}
      }}
      if(document.readyState!=='loading') run(); else document.addEventListener('DOMContentLoaded', run);
    }})();
    </script>

    <script src="/static/vis-embed.js" defer></script>
  </body>
</html>
""".format(
        title=title,
        css_href=css_href,
        bg=bg,
        theme=theme,
        debug_display="inline-block" if debug else "none",
        debug_bool=str(debug).lower(),
        source=source,
        js_href=js_href,
        embedded_block=embedded_block,
    )

    response.headers["Content-Security-Policy"] = csp
    response.headers["X-Content-Type-Options"] = "nosniff"
    return HTMLResponse(content=html, status_code=200)

# -----------------------------------------------------------------------------
# PYVIS
# -----------------------------------------------------------------------------
@app.get(
    "/v1/vis/pyvis",
    response_class=HTMLResponse,
    summary="Visualização com PyVis (fotos circulares, fallback ícone, cores CV/PCC, arestas finas, drag isolado, busca)",
    tags=["viz"]
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
    enrich_photos: bool = Query(default=True),
):
    try:
        data = await fetch_graph_sanitized(faccao_id, include_co, max_pairs, use_cache=cache, enrich_photos=enrich_photos)
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

    def is_person(t: Optional[str]) -> bool:
        t2 = (t or "").lower()
        return "pessoa" in t2 or "membro" in t2

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
        photo = n.get("photo_url")
        if photo and not (str(photo).startswith(("http://", "https://", "data:", "/assets/"))):
            photo = resolve_photo_url(str(photo))

        fixed_color = color_from_faccao(group)
        color = fixed_color or hash_color(group)

        node_kwargs = dict(title=label, color=color, borderWidth=1)
        if isinstance(size, (int, float)):
            node_kwargs["value"] = float(size)

        if photo:
            node_kwargs["shape"] = "circularImage"
            node_kwargs["image"] = photo
        elif is_person(n.get("type")):
            node_kwargs["shape"] = "image"
            node_kwargs["image"] = "/static/icons/person.svg"
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
            color = EDGE_COLORS.get(rel, "#90a4ae")
            net.add_edge(a, b, value=w, width=1, color=color, title=f"{rel} (w={w})")

    net.set_options("""
    const options = {
      interaction: {
        hover: true,
        dragNodes: true,
        dragView: false,
        zoomView: true,
        multiselect: true,
        navigationButtons: true
      },
      manipulation: { enabled: false },
      physics: {
        enabled: true,
        stabilization: { enabled: true, iterations: 300 },
        barnesHut: {
          gravitationalConstant: -8000,
          centralGravity: 0.2,
          springLength: 120,
          springConstant: 0.04,
          avoidOverlap: 0.2
        }
      },
      nodes: { shape: 'dot', borderWidth: 1 },
      edges: { smooth: false }
    };
    """)

    html = net.generate_html(title=title)

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
        // Desliga física após estabilização: arrastar não puxa o grafo todo
        if (window.network) {
          window.network.once('stabilizationIterationsDone', function(){ window.network.setOptions({ physics: false }); });
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
        function runSearch(txt){
          try{
            var ds = (typeof nodes !== 'undefined') ? nodes : (network && network.body && network.body.data && network.body.data.nodes);
            if (!ds) return;
            var all = ds.get();
            var t = (txt||'').trim().toLowerCase();
            if (!t){ return; }
            var hits = all.filter(function(n){ return (String(n.label||'').toLowerCase().indexOf(t) >= 0) || (String(n.id)===t); });
            if (!hits.length) return;

            all.forEach(function(n){ ds.update({ id: n.id, color: colorObj(n.color, 0.25) }); });
            hits.forEach(function(h){ var cur = ds.get(h.id); ds.update({ id: h.id, color: colorObj(cur.color, 1) }); });
            network.fit({ nodes: hits.map(function(h){return h.id;}), animation: { duration: 300 } });
          }catch(e){ console.error(e); }
        }
        var q = document.getElementById('kg-search');
        var p = document.getElementById('btn-print');
        var r = document.getElementById('btn-reload');
        if (p) p.onclick = function(){ window.print(); };
        if (r) r.onclick = function(){ location.reload(); };
        if (q){
          q.addEventListener('change', function(){ runSearch(q.value); });
          q.addEventListener('keyup', function(e){ if(e.key==='Enter') runSearch(q.value); });
        }
      })();
    </script>
    """
    html = html.replace("</head>", toolbar_css + "\n</head>")
    html = html.replace("<body>", "<body>\n" + toolbar_html + "\n")
    html = html.replace("</body>", toolbar_js + "\n</body>")
    return HTMLResponse(content=html, status_code=200)

# -----------------------------------------------------------------------------
# /docs custom (Swagger + painel de status)
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
      .ops-bar { padding: 12px 16px; border-bottom: 1px solid #eee; background:#fafafa;
                 display: grid; grid-template-columns: 1fr auto; gap: 12px; align-items: center; font-family: ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial; }
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
        <div class="note">Painel de status + Swagger UI.</div>
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

        const pills = document.querySelectorAll('.ops-right .ops-pill');
        pills.forEach(p => p.classList.remove('ok','err'));
        if (health && health.ok){ pills.forEach(p => p.classList.add('ok')); }
        else { pills.forEach(p => p.classList.add('err')); }
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
