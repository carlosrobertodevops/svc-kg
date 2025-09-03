# app.py (v1.7.9)
import os, json, hashlib, asyncio, logging, traceback, socket, math, csv, io
from typing import List, Optional, Tuple
from time import time
from urllib.parse import urlparse

from fastapi import FastAPI, Query, Response, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, ConfigDict
import yaml
import httpx
from psycopg_pool import AsyncConnectionPool
from redis import asyncio as aioredis
from pyvis.network import Network
import networkx as nx  # NEW

# ------------------ utils/env ------------------
def env_bool(v: Optional[str], default=False) -> bool:
    if v is None: return default
    return v.lower() in ("1", "true", "yes", "on")

def normalize_supabase_url(u: str) -> str:
    if not u: return ""
    u = u.strip().replace("/rest/v1", "")
    while u.endswith("/"): u = u[:-1]
    if not (u.startswith("http://") or u.startswith("https://")):
        u = "https://" + u
    return u

def _now() -> float: return time()

APP_ENV = os.getenv("APP_ENV", "production")
PORT = int(os.getenv("PORT", "8080"))
WORKERS = int(os.getenv("WORKERS", "2"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "info").upper()

CORS_ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*")
CORS_ALLOW_CREDENTIALS = env_bool(os.getenv("CORS_ALLOW_CREDENTIALS", "false"))
CORS_ALLOW_HEADERS = os.getenv("CORS_ALLOW_HEADERS", "Authorization,Content-Type")
CORS_ALLOW_METHODS = os.getenv("CORS_ALLOW_METHODS", "GET,POST,OPTIONS")

SUPABASE_URL_RAW = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_URL = normalize_supabase_url(SUPABASE_URL_RAW)

SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
if not SUPABASE_SERVICE_KEY and SUPABASE_KEY:
    SUPABASE_SERVICE_KEY = SUPABASE_KEY
if not SUPABASE_ANON_KEY and SUPABASE_KEY:
    SUPABASE_ANON_KEY = SUPABASE_KEY

SUPABASE_RPC_FN = os.getenv("SUPABASE_RPC_FN", "get_graph_membros")
SUPABASE_TIMEOUT = float(os.getenv("SUPABASE_TIMEOUT", "15"))

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

CACHE_STATIC_MAX_AGE = int(os.getenv("CACHE_STATIC_MAX_AGE", "86400"))
CACHE_API_TTL = int(os.getenv("CACHE_API_TTL", "60"))

ENABLE_REDIS_CACHE = env_bool(os.getenv("ENABLE_REDIS_CACHE", "true"), True)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0").strip()

BACKEND_MODE = "supabase" if (SUPABASE_URL and SUPABASE_SERVICE_KEY) else ("postgres" if DATABASE_URL else "none")

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("svc-kg")

# ------------------ app ------------------
app = FastAPI(
    title="svc-kg",
    version="1.7.9",
    description="Microserviço de Knowledge Graph (membros, facções, funções)",
    default_response_class=ORJSONResponse,
    swagger_ui_parameters={
        "displayRequestDuration": True,
        "docExpansion": "none",
        "defaultModelsExpandDepth": -1,
        "defaultModelExpandDepth": 0,
    },
)

allow_origins = [o.strip() for o in (CORS_ALLOW_ORIGINS or "*").split(",")]
allow_methods = [m.strip() for m in (CORS_ALLOW_METHODS or "*").split(",")]
allow_headers = [h.strip() for h in (CORS_ALLOW_HEADERS or "*").split(",")]
app.add_middleware(CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=allow_methods,
    allow_headers=allow_headers,
)
app.add_middleware(GZipMiddleware, minimum_size=512)

if os.path.isdir("assets"): app.mount("/assets", StaticFiles(directory="assets"), name="assets")
if os.path.isdir("static"): app.mount("/static", StaticFiles(directory="static"), name="static")

http_client: Optional[httpx.AsyncClient] = None
pool: Optional[AsyncConnectionPool] = None
redis_client: Optional[aioredis.Redis] = None
_mem_cache: dict[str, Tuple[float, dict]] = {}

# ------------------ models ------------------
class Node(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    label: str
    type: str
    group: Optional[int] = None
    size: Optional[float] = None

class Edge(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: str
    target: str
    weight: Optional[float] = 1.0
    relation: Optional[str] = None

class GraphResponse(BaseModel):
    nodes: List[Node]
    edges: List[Edge]

# ------------------ lifecycle ------------------
@app.on_event("startup")
async def _startup():
    global http_client, pool, redis_client
    log.info(f"Starting svc-kg v{app.version} | mode={BACKEND_MODE} | port={PORT} | supabase={SUPABASE_URL}")
    if BACKEND_MODE == "supabase":
        api_key = SUPABASE_ANON_KEY or SUPABASE_SERVICE_KEY
        auth_key = SUPABASE_SERVICE_KEY
        http_client = httpx.AsyncClient(
            base_url=SUPABASE_URL,
            timeout=httpx.Timeout(SUPABASE_TIMEOUT),
            headers={"apikey": api_key, "Authorization": f"Bearer {auth_key}", "Content-Type": "application/json"},
        )
    if BACKEND_MODE == "postgres":
        pool = AsyncConnectionPool(conninfo=DATABASE_URL, min_size=0, max_size=10, kwargs={"autocommit": True})
    if ENABLE_REDIS_CACHE and REDIS_URL:
        try:
            redis_client = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
            await asyncio.wait_for(redis_client.ping(), timeout=2); log.info("Redis OK")
        except Exception as e:
            log.warning(f"Redis indisponível, fallback memória: {e}"); redis_client = None

@app.on_event("shutdown")
async def _shutdown():
    if http_client: await http_client.aclose()
    if pool: await pool.close()
    if redis_client: await redis_client.close()

# ------------------ errors ------------------
@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code,
                        content={"error": str(exc.detail), "status_code": exc.status_code, "path": str(request.url.path)})

@app.exception_handler(Exception)
async def unhandled_exc_handler(request: Request, exc: Exception):
    log.error("Unhandled exception: %s\n%s", exc, "".join(traceback.format_exc()[-1000:]))
    return JSONResponse(status_code=500,
                        content={"error": "internal_error", "message": str(exc)[:500], "path": str(request.url.path)})

# ------------------ cache helpers ------------------
def _now_ts(): return _now()

async def cache_get(key: str) -> Optional[dict]:
    if redis_client:
        try:
            raw = await redis_client.get(key)
            if raw: return json.loads(raw)
        except Exception: pass
    hit = _mem_cache.get(key)
    if not hit: return None
    ts, data = hit
    if _now_ts() - ts > CACHE_API_TTL:
        _mem_cache.pop(key, None); return None
    return data

async def cache_set(key: str, data: dict, ttl: int):
    if redis_client:
        try:
            await redis_client.set(key, json.dumps(data), ex=ttl); return
        except Exception: pass
    _mem_cache[key] = (_now_ts(), data)

def etag_for(data: dict) -> str:
    import orjson; return hashlib.sha1(orjson.dumps(data)).hexdigest()

# ------------------ label normalization ------------------
def _is_pg_text_array(s: str) -> bool:
    s = s.strip()
    return len(s) >= 2 and s[0] == "{" and s[-1] == "}"

def normalize_label(raw: Optional[str]) -> str:
    if not raw: return ""
    s = str(raw).strip()
    if not _is_pg_text_array(s): return s
    inner = s[1:-1]
    if inner == "": return ""
    try:
        reader = csv.reader(io.StringIO(inner))
        arr = next(reader)
        out = []
        for item in arr:
            if item is None: continue
            t = item.strip().strip('"')
            if t.lower() == "null": continue
            out.append(t)
        return ", ".join(out)
    except Exception:
        return inner.replace('"', '').strip()

def normalize_graph_labels(data: dict) -> dict:
    nodes = []
    for n in data.get("nodes", []) or []:
        lab = normalize_label(n.get("label"))
        if not lab: lab = str(n.get("id", ""))
        nodes.append({**n, "label": lab})
    return {"nodes": nodes, "edges": data.get("edges", []) or []}

# ------------------ graph helpers ------------------
def sanitize_graph(data: dict) -> dict:
    raw_nodes = data.get("nodes", []) or []
    raw_edges = data.get("edges", []) or []
    id_seen = set(); nodes: list[dict] = []
    for n in raw_nodes:
        nid = str(n.get("id", "")).strip()
        if not nid: continue
        if nid in id_seen:
            for i in range(len(nodes)):
                if nodes[i]["id"] == nid:
                    nodes[i] = {**nodes[i], **n, "id": nid}
                    break
        else:
            nodes.append({**n, "id": nid}); id_seen.add(nid)
    edges: list[dict] = []
    for e in raw_edges:
        s = str(e.get("source", "")).strip()
        t = str(e.get("target", "")).strip()
        if not s or not t: continue
        if s not in id_seen or t not in id_seen: continue
        try: w = float(e.get("weight", 1.0) if e.get("weight", 1.0) is not None else 1.0)
        except Exception: w = 1.0
        rel = e.get("relation"); rel = str(rel) if (rel is not None) else None
        edges.append({"source": s, "target": t, "weight": w, "relation": rel})
    return {"nodes": nodes, "edges": edges}

def truncate_preview(data: dict, max_nodes: int, max_edges: int) -> dict:
    nodes = data.get("nodes", []); edges = data.get("edges", [])
    if len(nodes) <= max_nodes and len(edges) <= max_edges: return data
    deg = {n["id"]: 0.0 for n in nodes}
    for e in edges:
        w = float(e.get("weight", 1.0) or 1.0)
        if e.get("source") in deg: deg[e["source"]] += w
        if e.get("target") in deg: deg[e["target"]] += w
    keep = {n["id"] for n in sorted(nodes, key=lambda n: deg.get(n["id"], 0.0), reverse=True)[:max_nodes]}
    edges2 = [e for e in edges if e.get("source") in keep and e.get("target") in keep]
    if len(edges2) > max_edges:
        edges2.sort(key=lambda e: float(e.get("weight", 1.0)), reverse=True)
        edges2 = edges2[:max_edges]
    nodes2 = [n for n in nodes if n["id"] in keep]
    return {"nodes": nodes2, "edges": edges2}

def _hash_color(key: str) -> str:
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return f"#{h[:6]}"

def _degree_map(nodes: list[dict], edges: list[dict]) -> dict[str, int]:
    deg = {n["id"]: 0 for n in nodes}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in deg: deg[s] += 1
        if t in deg: deg[t] += 1
    return deg

def _wrap_toolbar(html: str, title: str = "PyVis Graph", show_print_btn: bool = True) -> str:
    if not show_print_btn: return html
    toolbar = f"""
    <style>
      .kg-toolbar{{position:fixed;top:12px;right:12px;z-index:9999;background:rgba(0,0,0,.6);color:#fff;border-radius:10px;padding:6px 10px;font-family:Inter,system-ui,Arial}}
      .kg-toolbar button{{margin-left:6px;padding:6px 10px;border:0;border-radius:8px;cursor:pointer;background:#fff;color:#111}}
      .kg-toolbar h4{{margin:0 8px 0 0;display:inline;font-weight:600}}
      @media print{{.kg-toolbar{{display:none}}}}
      html,body,#notebook,#mynetwork{{height:100%}} body{{margin:0}}
    </style>
    <div class="kg-toolbar">
      <h4>{title.replace("<","&lt;").replace(">","&gt;")}</h4>
      <button onclick="window.print()">Print</button>
      <button onclick="location.reload()">Reload</button>
    </div>
    """
    return html.replace("<body>", "<body>" + toolbar, 1)

def _ensure_network_min_height(html: str, min_height: str = "90vh") -> str:
    marker = '<div id="mynetwork"'
    if marker in html and 'id="mynetwork" style=' not in html:
        html = html.replace(marker, f'{marker} style="height:{min_height}; width:100%;"', 1)
    return html

def _append_debug_overlay(html: str, n_nodes: int, n_edges: int) -> str:
    badge = f"""
    <div style="position:fixed;left:12px;bottom:12px;z-index:9999;background:rgba(0,0,0,.6);color:#fff;border-radius:10px;padding:6px 10px;font-family:Inter,system-ui,Arial">
      nodes: {n_nodes} · edges: {n_edges}
    </div>
    """
    return html.replace("</body>", badge + "\n</body>", 1)

# ------------------ build pyvis (clássico) ------------------
async def build_pyvis_html(data: dict, theme: str="light", arrows: bool=True,
                           hierarchical: bool=False, physics: bool=True,
                           barnes_hut: bool=True, show_buttons: bool=True,
                           title: str="Knowledge Graph") -> str:
    norm = normalize_graph_labels(data)
    nodes = norm.get("nodes", []); edges = norm.get("edges", [])
    bg = "#0b0f19" if theme == "dark" else "#ffffff"
    fg = "#e6e9ef" if theme == "dark" else "#111111"
    net = Network(height="100%", width="100%", bgcolor=bg, font_color=fg,
                  cdn_resources="in_line", notebook=False, directed=arrows)
    deg = _degree_map(nodes, edges)
    for n in nodes:
        nid = n.get("id"); label = n.get("label", nid)
        group = str(n.get("group", n.get("type", "0"))); ntype = n.get("type", "node")
        size = n.get("size")
        if size is None: size = 10 + math.log(deg.get(nid,0)+1)*8
        color = _hash_color(group)
        title_node = f"<b>{label}</b><br>id: {nid}<br>grupo: {group}<br>tipo: {ntype}"
        net.add_node(nid, label=label, title=title_node, color=color, size=size, shape="dot")
    for e in edges:
        s = e.get("source"); t = e.get("target"); w = float(e.get("weight", 1.0) or 1.0)
        rel = e.get("relation", "")
        title_edge = f"{rel} (w={w})" if rel else f"w={w}"
        net.add_edge(s, t, title=title_edge, value=w, arrows="to" if arrows else "", physics=physics)
    if show_buttons:
        net.show_buttons(filter_=["physics","interaction","layout"])
    import json as _json
    options = {
        "interaction":{"hover":True,"dragNodes":True,"dragView":True,"zoomView":True,"multiselect":True,"navigationButtons":True},
        "manipulation":{"enabled":False},
        "physics":{"enabled":physics,"stabilization":{"enabled":True,"iterations":500}},
        "nodes":{"borderWidth":1,"shape":"dot"},
        "edges":{"smooth":False}
    }
    if barnes_hut:
        options["physics"]["barnesHut"] = {"gravitationalConstant":-8000,"centralGravity":0.2,"springLength":120,"springConstant":0.04,"avoidOverlap":0.2}
    if hierarchical:
        options["layout"] = {"hierarchical":{"enabled":True,"direction":"UD","sortMethod":"hubsize","nodeSpacing":200,"levelSeparation":200}}
        options["physics"]["enabled"] = False
    net.set_options(_json.dumps(options))
    net.generate_html()
    return net.html

# ------------------ build pyvis PRO (NetworkX) ------------------
async def build_pyvis_html_repo_style(
    data: dict,
    theme: str = "light",
    arrows: bool = True,
    physics: bool = True,
    show_buttons: bool = True,
    metric: str = "degree",     # "degree" | "betweenness"
    communities: bool = True,   # colorir por comunidades
    title: str = "Knowledge Graph (PyVis+NX)"
) -> str:
    norm = normalize_graph_labels(data)
    nodes = norm.get("nodes", []) or []
    edges = norm.get("edges", []) or []

    bg = "#0b0f19" if theme == "dark" else "#ffffff"
    fg = "#e6e9ef" if theme == "dark" else "#111111"

    G = nx.Graph()
    for n in nodes:
        nid = str(n.get("id", "")).strip()
        if not nid: continue
        G.add_node(
            nid,
            label=n.get("label") or nid,
            group=str(n.get("group", n.get("type", "0"))),
            ntype=n.get("type", "node"),
            size=n.get("size")
        )
    for e in edges:
        s = str(e.get("source", "")).strip()
        t = str(e.get("target", "")).strip()
        if not s or not t or (s not in G) or (t not in G): continue
        w = float(e.get("weight", 1.0) or 1.0)
        rel = e.get("relation", "")
        G.add_edge(s, t, weight=w, title=(f"{rel} (w={w})" if rel else f"w={w}"))

    sizes = {}
    try:
        if metric == "betweenness":
            c = nx.betweenness_centrality(G, normalized=True)
            sizes = {n: 10 + (v * 80.0) for n, v in c.items()}
        else:
            c = nx.degree_centrality(G)
            sizes = {n: 10 + (v * 80.0) for n, v in c.items()}
    except Exception:
        deg = dict(G.degree())
        sizes = {n: 10 + (math.log(deg.get(n, 0) + 1) * 8.0) for n in G.nodes()}

    comm_map = {}
    try:
        from networkx.algorithms.community import greedy_modularity_communities
        comms = list(greedy_modularity_communities(G)) if communities else []
        for idx, com in enumerate(comms):
            for n in com: comm_map[n] = idx
    except Exception:
        comm_map = {}

    def hash_color(key: str) -> str:
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return f"#{h[:6]}"

    net = Network(height="100%", width="100%", bgcolor=bg, font_color=fg,
                  cdn_resources="in_line", notebook=False, directed=arrows)

    for n in G.nodes():
        attrs = G.nodes[n]
        label = attrs.get("label", n)
        group = attrs.get("group", "0")
        ntype = attrs.get("ntype", "node")
        size = attrs.get("size")
        if size is None: size = sizes.get(n, 12.0)

        color = hash_color(f"c{comm_map[n]}") if n in comm_map else hash_color(str(group))
        title_node = f"<b>{label}</b><br>id: {n}<br>grupo: {group}<br>tipo: {ntype}"
        net.add_node(n, label=label, title=title_node, color=color, size=size, shape="dot")

    for s, t, ed in G.edges(data=True):
        net.add_edge(s, t, title=ed.get("title"), value=float(ed.get("weight", 1.0) or 1.0),
                     arrows="to" if arrows else "", physics=physics)

    if show_buttons:
        net.show_buttons(filter_=["physics", "interaction", "layout"])

    import json as _json
    options = {
        "interaction": {"hover": True, "dragNodes": True, "dragView": True, "zoomView": True,
                        "multiselect": True, "navigationButtons": True},
        "manipulation": {"enabled": False},
        "physics": {"enabled": physics, "stabilization": {"enabled": True, "iterations": 500}},
        "nodes": {"borderWidth": 1, "shape": "dot"},
        "edges": {"smooth": False}
    }
    if physics:
        options["physics"]["barnesHut"] = {
            "gravitationalConstant": -8000, "centralGravity": 0.2,
            "springLength": 120, "springConstant": 0.04, "avoidOverlap": 0.2
        }
    net.set_options(_json.dumps(options))
    net.generate_html()
    html = net.html
    html = _wrap_toolbar(html, title=title, show_print_btn=True)
    html = _ensure_network_min_height(html, min_height="92vh")
    return html

# ------------------ backends ------------------
async def fetch_graph_via_supabase(faccao_id: Optional[int], include_co: bool, max_pairs: int) -> dict:
    if http_client is None: raise HTTPException(status_code=503, detail="Supabase client não inicializado")
    url = f"/rest/v1/rpc/{SUPABASE_RPC_FN}"
    body = {"p_faccao_id": faccao_id, "p_include_co": include_co, "p_max_pairs": max_pairs}
    r = await http_client.post(url, json=body)
    if r.status_code >= 400: raise HTTPException(status_code=502, detail=f"Supabase RPC {SUPABASE_RPC_FN} falhou ({r.status_code}): {r.text[:400]}")
    try: data = r.json() if r.content else {"nodes": [], "edges": []}
    except Exception: data = {"nodes": [], "edges": []}
    if isinstance(data, str):
        try: data = json.loads(data)
        except Exception: data = {"nodes": [], "edges": []}
    data.setdefault("nodes", []); data.setdefault("edges", [])
    return data

async def fetch_graph_via_pg(faccao_id: Optional[int], include_co: bool, max_pairs: int) -> dict:
    if pool is None: raise HTTPException(status_code=503, detail="Pool Postgres não inicializado")
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute("select public.et_graph_membros(%s,%s,%s);", (faccao_id, include_co, max_pairs))
                row = await cur.fetchone()
            except Exception:
                await cur.execute("select public.get_graph_membros(%s,%s,%s);", (faccao_id, include_co, max_pairs))
                row = await cur.fetchone()
            data = row[0] if row else {"nodes": [], "edges": []}
            if isinstance(data, str):
                try: data = json.loads(data)
                except Exception: data = {"nodes": [], "edges": []}
            data.setdefault("nodes", []); data.setdefault("edges", [])
            return data

async def fetch_graph_raw(faccao_id: Optional[int], include_co: bool, max_pairs: int) -> dict:
    if BACKEND_MODE == "supabase": return await fetch_graph_via_supabase(faccao_id, include_co, max_pairs)
    if BACKEND_MODE == "postgres": return await fetch_graph_via_pg(faccao_id, include_co, max_pairs)
    raise HTTPException(status_code=500, detail="Nenhum backend configurado (defina SUPABASE_* ou DATABASE_URL)")

async def fetch_graph_sanitized(faccao_id: Optional[int], include_co: bool, max_pairs: int, use_cache: bool=True) -> dict:
    key = f"graph_raw:{faccao_id}:{include_co}:{max_pairs}"
    data = await cache_get(key) if use_cache else None
    if data is None:
        raw = await fetch_graph_raw(faccao_id, include_co, max_pairs)
        data = sanitize_graph(raw)
        if use_cache: await cache_set(key, data, CACHE_API_TTL)
    return data

# ------------------ openapi ------------------
def custom_openapi():
    if getattr(app, "openapi_schema", None): return app.openapi_schema
    yaml_path = os.path.join("docs", "openapi.yaml")
    if os.path.exists(yaml_path):
        with open(yaml_path, "r", encoding="utf-8") as f:
            app.openapi_schema = yaml.safe_load(f); return app.openapi_schema
    app.openapi_schema = get_openapi(title=app.title, version=app.version, routes=app.routes, description=app.description)
    return app.openapi_schema
app.openapi = custom_openapi

# ------------------ basic endpoints ------------------
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'><title>svc-kg</title>"
        "<style>body{font-family:Inter,system-ui,Arial;padding:24px}</style>"
        "</head><body><h1>svc-kg online</h1>"
        "<p>Use <code>/live</code>, <code>/ready</code>, <code>/v1/graph/membros</code>, "
        "<code>/v1/vis/pyvis</code>, <code>/v1/vis/pyvis2</code> ou <code>/v1/vis/visjs</code>.</p></body></html>"
    )

@app.get("/live", summary="Liveness")
async def live(): return {"status": "live", "service": "svc-kg"}

@app.get("/ready", summary="Readiness (checa DNS/Redis/backend)")
async def ready():
    info = {"redis": False, "backend": BACKEND_MODE, "backend_ok": False}
    if BACKEND_MODE == "supabase":
        parsed = urlparse(SUPABASE_URL); host = parsed.hostname or ""
        info["supabase_host"] = host
        try:
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(loop.run_in_executor(None, socket.getaddrinfo, host, 443), timeout=2)
            info["dns_ok"] = True
        except Exception as e:
            info["dns_ok"] = False; info["error"] = f"DNS fail for {host}: {e}"
            return ORJSONResponse(info, status_code=503)
    if redis_client:
        try: info["redis"] = bool(await asyncio.wait_for(redis_client.ping(), timeout=1.5))
        except Exception: info["redis"] = False
    try:
        _ = await fetch_graph_sanitized(None, False, 1, use_cache=False)
        info["backend_ok"] = True
    except Exception as e:
        info["backend_ok"] = False; info["error"] = str(e)[:400]
    return ORJSONResponse(info, status_code=(200 if info["backend_ok"] else 503))

@app.get("/debug/config", summary="Config (sanitizada)")
async def debug_config():
    return {
        "env": APP_ENV, "port": PORT, "backend_mode": BACKEND_MODE,
        "supabase_url_raw": SUPABASE_URL_RAW, "supabase_url": SUPABASE_URL,
        "has_anon_key": bool(SUPABASE_ANON_KEY), "has_service_key": bool(SUPABASE_SERVICE_KEY),
        "rpc_function": SUPABASE_RPC_FN, "has_database_url": bool(DATABASE_URL),
        "redis_enabled": ENABLE_REDIS_CACHE, "has_redis_url": bool(REDIS_URL),
    }

@app.get("/health", summary="Health (estático)")
async def health(response: Response):
    response.headers["Cache-Control"] = f"public, max-age={CACHE_STATIC_MAX_AGE}"
    return {"status":"ok","service":"svc-kg","env":APP_ENV,
            "cache":"redis" if (ENABLE_REDIS_CACHE and REDIS_URL and redis_client) else "memory",
            "backend": BACKEND_MODE}

# ------------------ graph endpoints ------------------
@app.get("/v1/graph/membros", response_model=GraphResponse, summary="Grafo (via et_graph_membros/get_graph_membros)")
async def graph_membros(
    response: Response,
    faccao_id: Optional[int] = Query(default=None),
    include_co: bool = Query(default=True),
    max_pairs: int = Query(default=8000, ge=1, le=200000),
    max_nodes: int = Query(default=2000, ge=100, le=20000),
    max_edges: int = Query(default=4000, ge=100, le=200000),
    cache: bool = Query(default=True)
):
    key = f"graph_sane:{faccao_id}:{include_co}:{max_pairs}"
    data = await cache_get(key) if cache else None
    if data is None:
        data = await fetch_graph_sanitized(faccao_id, include_co, max_pairs, use_cache=True)
        if cache: await cache_set(key, data, CACHE_API_TTL)
    out = truncate_preview(data, max_nodes, max_edges)
    response.headers["ETag"] = etag_for(out); response.headers["Cache-Control"] = "public, max-age=30"
    return out

@app.get("/graph/members", response_model=GraphResponse, summary="(Compat) /v1/graph/membros com p_*")
async def graph_members_compat(
    response: Response,
    p_faccao_id: Optional[int] = Query(default=None),
    p_include_co: bool = Query(default=True),
    p_max_pairs: int = Query(default=8000, ge=1, le=200000),
    max_nodes: int = Query(default=2000, ge=100, le=20000),
    max_edges: int = Query(default=4000, ge=100, le=200000),
    cache: bool = Query(default=True),
):
    key = f"graph_sane:{p_faccao_id}:{p_include_co}:{p_max_pairs}"
    data = await cache_get(key) if cache else None
    if data is None:
        data = await fetch_graph_sanitized(p_faccao_id, p_include_co, p_max_pairs, use_cache=True)
        if cache: await cache_set(key, data, CACHE_API_TTL)
    out = truncate_preview(data, max_nodes, max_edges)
    response.headers["ETag"] = etag_for(out); response.headers["Cache-Control"] = "public, max-age=30"
    return out

@app.get("/v1/nodes/{node_id}/neighbors", response_model=GraphResponse, summary="Subgrafo (raio=1)")
async def neighbors(response: Response, node_id: str, include_co: bool = True, max_pairs: int = 3000):
    data = await fetch_graph_sanitized(None, include_co, max_pairs, use_cache=True)
    nodes = data.get("nodes", []); edges = data.get("edges", [])
    keep = {node_id}
    for e in edges:
        if e.get("source") == node_id: keep.add(e.get("target"))
        if e.get("target") == node_id: keep.add(e.get("source"))
    nodes2 = [n for n in nodes if n.get("id") in keep]
    edges2 = [e for e in edges if e.get("source") in keep and e.get("target") in keep]
    out = {"nodes": nodes2, "edges": edges2}
    response.headers["ETag"] = etag_for(out); response.headers["Cache-Control"] = "public, max-age=30"
    return out

# ------------------ VIS: PyVis (inline clássico) ------------------
@app.get("/v1/vis/pyvis", response_class=HTMLResponse, summary="Visualização PyVis (inline JS)")
async def vis_pyvis(
    response: Response,
    faccao_id: Optional[int] = Query(default=None),
    include_co: bool = Query(default=True),
    max_pairs: int = Query(default=8000, ge=1, le=200000),
    max_nodes: int = Query(default=2000, ge=100, le=20000),
    max_edges: int = Query(default=4000, ge=100, le=200000),
    cache: bool = Query(default=True),
    theme: str = Query(default="light", pattern="^(light|dark)$"),
    arrows: bool = True,
    hierarchical: bool = False,
    physics: bool = True,
    barnes_hut: bool = True,
    show_buttons: bool = True,
    title: str = "Knowledge Graph",
    toolbar: bool = True,
    allow_inline: bool = Query(default=True),
    meta_csp: bool = Query(default=True),
    min_height: str = Query(default="90vh"),
    debug: bool = Query(default=False)
):
    key = f"graph_sane:{faccao_id}:{include_co}:{max_pairs}"
    data = await cache_get(key) if cache else None
    if data is None:
        data = await fetch_graph_sanitized(faccao_id, include_co, max_pairs, use_cache=True)
        if cache: await cache_set(key, data, CACHE_API_TTL)
    out = truncate_preview(data, max_nodes, max_edges)

    if not out.get("nodes"):
        empty = f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>html,body{{height:100%;margin:0}}.empty{{display:flex;height:100%;align-items:center;justify-content:center;font-family:Inter,Arial,sans-serif;background:{'#0b0f19' if theme=='dark' else '#fff'};color:{'#e6e9ef' if theme=='dark' else '#111'}}}</style></head>
<body><div class="empty">Nenhum dado para exibir (nodes=0).</div></body></html>"""
        if allow_inline:
            response.headers["Content-Security-Policy"] = "default-src 'self' data: blob:; style-src 'self' 'unsafe-inline' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; img-src * data: blob:; font-src 'self' data:; connect-src *;"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return HTMLResponse(content=empty, status_code=200)

    html = await build_pyvis_html(out, theme=theme, arrows=arrows, hierarchical=hierarchical, physics=physics, barnes_hut=barnes_hut, show_buttons=show_buttons, title=title)
    if meta_csp:
        meta = "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'self' data: blob:; style-src 'self' 'unsafe-inline' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; img-src * data: blob:; font-src 'self' data:; connect-src *;\">"
        html = html.replace("<head>", "<head>" + meta, 1)
    html = _ensure_network_min_height(html, min_height=min_height)
    if toolbar: html = _wrap_toolbar(html, title=title, show_print_btn=True)
    if debug: html = _append_debug_overlay(html, len(out["nodes"]), len(out["edges"]))
    if allow_inline:
        response.headers["Content-Security-Policy"] = "default-src 'self' data: blob:; style-src 'self' 'unsafe-inline' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; img-src * data: blob:; font-src 'self' data:; connect-src *;"
    response.headers["ETag"] = etag_for(out)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return HTMLResponse(content=html, status_code=200)

# ------------------ VIS: PyVis PRO (NetworkX) ------------------
@app.get("/v1/vis/pyvis2", response_class=HTMLResponse,
         summary="Visualização PyVis com NetworkX (centralidade + comunidades)")
async def vis_pyvis2(
    response: Response,
    faccao_id: Optional[int] = Query(default=None),
    include_co: bool = Query(default=True),
    max_pairs: int = Query(default=8000, ge=1, le=200000),
    max_nodes: int = Query(default=2000, ge=100, le=20000),
    max_edges: int = Query(default=4000, ge=100, le=200000),
    cache: bool = Query(default=True),
    theme: str = Query(default="light", pattern="^(light|dark)$"),
    arrows: bool = True,
    physics: bool = True,
    metric: str = Query(default="degree", pattern="^(degree|betweenness)$"),
    communities: bool = True,
    title: str = "Knowledge Graph (PyVis+NX)",
    debug: bool = False
):
    key = f"graph_sane:{faccao_id}:{include_co}:{max_pairs}"
    data = await cache_get(key) if cache else None
    if data is None:
        data = await fetch_graph_sanitized(faccao_id, include_co, max_pairs, use_cache=True)
        if cache:
            await cache_set(key, data, CACHE_API_TTL)
    out = truncate_preview(data, max_nodes, max_edges)

    if not out.get("nodes"):
        html = """<!doctype html><html><head><meta charset="utf-8"><title>PyVis</title>
<style>html,body{height:100%;margin:0}body{display:flex;align-items:center;justify-content:center;font-family:Inter,Arial}</style></head>
<body>Sem dados (nodes=0).</body></html>"""
        return HTMLResponse(content=html, status_code=200)

    try:
        html = await build_pyvis_html_repo_style(
            out, theme=theme, arrows=arrows, physics=physics,
            metric=metric, communities=communities, title=title
        )
    except Exception:
        html = await build_pyvis_html(out, theme=theme, arrows=arrows, physics=physics,
                                      barnes_hut=True, show_buttons=True, title=title)

    response.headers["Content-Security-Policy"] = (
        "default-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline' data: blob:; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; "
        "img-src * data: blob:; font-src 'self' data:; connect-src *;"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return HTMLResponse(content=html, status_code=200)

# ------------------ VIS: vis-network (server-embed + fallback inline seguro) ------------------
@app.get("/v1/vis/visjs", response_class=HTMLResponse,
         summary="Visualização vis-network (server-embed + fallback inline)")
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
    source: str = Query(default="server", pattern="^(server|client)$")
):
    local_js = "static/vendor/vis-network/vis-network.min.js"
    local_css = "static/vendor/vis-network/vis-network.min.css"
    has_local = os.path.exists(local_js) and os.path.exists(local_css)
    if has_local:
        js_href = "/static/vendor/vis-network/vis-network.min.js"
        css_href = "/static/vendor/vis-network/vis-network.min.css"
        csp = ("default-src 'self'; style-src 'self' 'unsafe-inline'; "
               "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
               "font-src 'self' data:; connect-src 'self';")
    else:
        js_href = "https://unpkg.com/vis-network@9.1.6/dist/vis-network.min.js"
        css_href = "https://unpkg.com/vis-network@9.1.6/styles/vis-network.min.css"
        csp = ("default-src 'self'; style-src 'self' 'unsafe-inline' https://unpkg.com; "
               "script-src 'self' 'unsafe-inline' https://unpkg.com; "
               "img-src 'self' data:; font-src 'self' data:; connect-src 'self';")

    embedded_block = ""
    if source == "server":
        data = await fetch_graph_sanitized(faccao_id, include_co, max_pairs, use_cache=cache)
        out = truncate_preview(data, max_nodes, max_edges)
        out = normalize_graph_labels(out)
        json_str = json.dumps(out, ensure_ascii=False).replace("</script>", "<\\/script>")
        embedded_block = '<script id="__KG_DATA__" type="application/json">' + json_str + '</script>'

    bg = "#0b0f19" if theme == "dark" else "#ffffff"
    debug_style = "inline-block" if debug else "none"

    tpl = """<!doctype html>
<html lang="pt-br">
  <head>
    <meta charset="utf-8" />
    <meta http-equiv="x-ua-compatible" content="ie=edge" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>%TITLE%</title>
    <link rel="stylesheet" href="%CSS%">
    <link rel="stylesheet" href="/static/vis-style.css">
    <meta name="theme-color" content="%BG%">
    <style>html,body,#mynetwork{height:100%;margin:0}</style>
  </head>
  <body data-theme="%THEME%">
    <div class="kg-toolbar">
      <h4>%TITLE%</h4>
      <button id="btn-print" type="button" title="Imprimir">Print</button>
      <button id="btn-reload" type="button" title="Recarregar">Reload</button>
      <span id="badge" class="badge" style="display:%DBG_STYLE%">debug</span>
    </div>
    <div id="mynetwork"
         style="height:90vh;width:100%;"
         data-endpoint="/v1/graph/membros"
         data-debug="%DEBUG%"
         data-source="%SOURCE%"></div>
    %EMBED%
    <script src="%JS%" crossorigin="anonymous"></script>

    <script>
    (function(){
      const container = document.getElementById('mynetwork');
      const source = container.getAttribute('data-source') || 'server';
      const debug = container.getAttribute('data-debug') === 'true';
      const endpoint = container.getAttribute('data-endpoint') || '/v1/graph/membros';

      function hashColor(s){
        s = String(s||''); let h=0; for (let i=0;i<s.length;i++) { h=(h<<5)-h+s.charCodeAt(i); h|=0; }
        const hue=Math.abs(h)%360; return `hsl(${hue},70%,50%)`;
      }
      function isPgTextArray(s){ s=(s||'').trim(); return s.length>=2 && s[0]==='{' && s[s.length-1]==='}'; }
      function cleanLabel(raw){
        if(!raw) return '';
        const s=String(raw).trim();
        if(!isPgTextArray(s)) return s;
        const inner=s.slice(1,-1);
        if(!inner) return '';
        return inner.replace(/(^|,)\\s*"?null"?\\s*(?=,|$)/gi,'')
                    .replace(/"/g,'')
                    .split(',').map(x=>x.trim()).filter(Boolean).join(', ');
      }
      function degreeMap(nodes,edges){
        const d={}; nodes.forEach(n=>d[n.id]=0);
        edges.forEach(e=>{ if(e.from in d) d[e.from]++; if(e.to in d) d[e.to]++; });
        return d;
      }
      function attachToolbar(net,nc,ec){
        const p=document.getElementById('btn-print'); if(p) p.onclick=()=>window.print();
        const r=document.getElementById('btn-reload'); if(r) r.onclick=()=>location.reload();
        const b=document.getElementById('badge'); if(b && debug) b.textContent=`nodes: ${nc} · edges: ${ec}`;
      }
      function render(data){
        const nodes=(data.nodes||[]).filter(n=>n&&n.id).map(n=>({
          id:String(n.id),
          label: cleanLabel(n.label)||String(n.id),
          group: String(n.group ?? n.type ?? '0'),
          value: (typeof n.size==='number') ? n.size : undefined,
          color: hashColor(n.group ?? n.type ?? '0'),
          shape: 'dot'
        }));
        const edges=(data.edges||[]).filter(e=>e&&e.source&&e.target).map(e=>({
          from:String(e.source), to:String(e.target),
          value: (e.weight!=null? Number(e.weight):1.0),
          title: e.relation ? `${e.relation} (w=${e.weight ?? 1})` : `w=${e.weight ?? 1}`
        }));

        if(!nodes.length){
          container.innerHTML='<div style="display:flex;height:100%;align-items:center;justify-content:center;opacity:.85">Nenhum dado para exibir (nodes=0).</div>';
          return;
        }
        const hasSize = nodes.some(n=>typeof n.value==='number');
        if(!hasSize) {
          const deg=degreeMap(nodes,edges);
          nodes.forEach(n=>{ const d=deg[n.id]||0; n.value=10+Math.log(d+1)*8; });
        }
        const dsNodes=new vis.DataSet(nodes);
        const dsEdges=new vis.DataSet(edges);
        const options={ interaction:{hover:true,dragNodes:true,dragView:true,zoomView:true,multiselect:true,navigationButtons:true},
                        manipulation:{enabled:false},
                        physics:{enabled:true,stabilization:{enabled:true,iterations:500},
                                 barnesHut:{gravitationalConstant:-8000,centralGravity:0.2,springLength:120,springConstant:0.04,avoidOverlap:0.2}},
                        nodes:{borderWidth:1,shape:'dot'}, edges:{smooth:false,arrows:{to:{enabled:true}}} };
        const net=new vis.Network(container,{nodes:dsNodes,edges:dsEdges},options);
        net.once('stabilizationIterationsDone',()=>net.fit({animation:{duration:300}}));
        net.on('doubleClick',()=>net.fit({animation:{duration:300}}));
        attachToolbar(net,nodes.length,edges.length);
      }

      function run(){
        if(typeof vis==='undefined') {
          container.innerHTML='<div style="padding:12px">vis-network não carregou. Verifique CSP/CDN.</div>';
          return;
        }
        if(source==='server'){
          const tag=document.getElementById('__KG_DATA__');
          if(!tag) { container.innerHTML='<div style="padding:12px">Bloco de dados ausente.</div>'; return; }
          try { render(JSON.parse(tag.textContent||'{}')); }
          catch(e){ console.error(e); container.innerHTML='<pre>'+String(e)+'</pre>'; }
        } else {
          const params=new URLSearchParams(window.location.search);
          const qs=new URLSearchParams();
          const fac=params.get('faccao_id'); if(fac && fac.trim()!=='') qs.set('faccao_id',fac.trim());
          qs.set('include_co', params.get('include_co') ?? 'true');
          qs.set('max_pairs',  params.get('max_pairs')  ?? '8000');
          qs.set('max_nodes',  params.get('max_nodes')  ?? '2000');
          qs.set('max_edges',  params.get('max_edges')  ?? '4000');
          qs.set('cache',      params.get('cache')      ?? 'false');
          const url=endpoint+'?'+qs.toString();
          fetch(url,{headers:{'Accept':'application/json'}})
            .then(async r=>{ if(!r.ok) throw new Error(r.status+': '+await r.text()); return r.json(); })
            .then(render)
            .catch(err=>{ console.error(err); container.innerHTML='<pre>'+String(err).replace(/</g,'&lt;')+'</pre>'; });
        }
      }
      if(document.readyState!=='loading') run(); else document.addEventListener('DOMContentLoaded', run);
    })();
    </script>

    <script src="/static/vis-embed.js" defer></script>
  </body>
</html>
"""
    html = (tpl
            .replace("%TITLE%", title)
            .replace("%CSS%", css_href)
            .replace("%JS%", js_href)
            .replace("%BG%", bg)
            .replace("%THEME%", theme)
            .replace("%DBG_STYLE%", debug_style)
            .replace("%DEBUG%", str(debug).lower())
            .replace("%SOURCE%", source)
            .replace("%EMBED%", embedded_block))

    response.headers["Content-Security-Policy"] = csp
    response.headers["X-Content-Type-Options"] = "nosniff"
    return HTMLResponse(content=html, status_code=200)