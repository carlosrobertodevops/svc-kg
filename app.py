import json
import math
import os
from typing import Any, Dict, List, Optional

import httpx
import redis
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

APP_VERSION = "1.7.12-safe"


# -----------------------------------------------------------------------------
# Config helpers
# -----------------------------------------------------------------------------
def env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")


APP_ENV = os.getenv("APP_ENV", "production")
PORT = int(os.getenv("PORT", "8080"))

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
SUPABASE_RPC_FN = os.getenv("SUPABASE_RPC_FN", "get_graph_membros")
SUPABASE_TIMEOUT = int(os.getenv("SUPABASE_TIMEOUT", "15"))

ENABLE_REDIS_CACHE = env_bool("ENABLE_REDIS_CACHE", True)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CACHE_API_TTL = int(os.getenv("CACHE_API_TTL", "60"))

# -----------------------------------------------------------------------------
# App
# -----------------------------------------------------------------------------
app = FastAPI(
    title="svc-kg",
    version=APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    swagger_ui_parameters={"defaultModelsExpandDepth": -1},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_credentials=env_bool("CORS_ALLOW_CREDENTIALS", False),
    allow_methods=os.getenv("CORS_ALLOW_METHODS", "GET,POST,OPTIONS").split(","),
    allow_headers=os.getenv("CORS_ALLOW_HEADERS", "Authorization,Content-Type").split(","),
)

if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# Redis opcional
redis_client: Optional[redis.Redis] = None
if ENABLE_REDIS_CACHE:
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        redis_client.ping()
    except Exception:
        redis_client = None


# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------
def normalize_label(raw: Any) -> str:
    if raw is None:
        return ""
    s = str(raw).strip()
    if len(s) >= 2 and s[0] == "{" and s[-1] == "}":
        inner = s[1:-1]
        if not inner:
            return ""
        parts = [p.strip().strip('"') for p in inner.split(",")]
        parts = [p for p in parts if p and p.lower() != "null"]
        return ", ".join(parts)
    return s


def sanitize_graph(data: Dict[str, Any]) -> Dict[str, Any]:
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []

    out_nodes: List[Dict[str, Any]] = []
    seen = set()
    for n in nodes:
        if not n:
            continue
        nid = str(n.get("id") or "")
        if not nid or nid in seen:
            continue
        seen.add(nid)
        out_nodes.append(
            {
                "id": nid,
                "label": normalize_label(n.get("label")) or nid,
                "type": n.get("type") or "membro",
                "group": n.get("group") if n.get("group") is not None else n.get("faccao_id") or 0,
                "size": float(n.get("size")) if isinstance(n.get("size"), (int, float)) else None,
            }
        )

    out_edges: List[Dict[str, Any]] = []
    for e in edges:
        if not e:
            continue
        s = e.get("source")
        t = e.get("target")
        if s is None or t is None:
            continue
        out_edges.append(
            {
                "source": str(s),
                "target": str(t),
                "weight": float(e.get("weight")) if e.get("weight") is not None else 1.0,
                "relation": e.get("relation") or "",
            }
        )

    return {"nodes": out_nodes, "edges": out_edges}


def truncate_graph(data: Dict[str, Any], max_nodes: int, max_edges: int) -> Dict[str, Any]:
    nodes = (data.get("nodes") or [])[: max(0, max_nodes)]
    node_ids = {n["id"] for n in nodes}
    edges = [
        e for e in (data.get("edges") or []) if e["source"] in node_ids and e["target"] in node_ids
    ]
    edges = edges[: max(0, max_edges)]
    return {"nodes": nodes, "edges": edges}


def degree_based_size(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> None:
    deg = {n["id"]: 0 for n in nodes}
    for e in edges:
        if e["source"] in deg:
            deg[e["source"]] += 1
        if e["target"] in deg:
            deg[e["target"]] += 1
    for n in nodes:
        if n.get("size") is None:
            d = deg.get(n["id"], 0)
            n["size"] = 10 + math.log(d + 1) * 8


async def supabase_rpc(
    faccao_id: Optional[int], include_co: bool, max_pairs: int
) -> Dict[str, Any]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(status_code=503, detail="Supabase não configurado")

    url = f"{SUPABASE_URL}/rest/v1/rpc/{SUPABASE_RPC_FN}"
    payload = {"p_faccao_id": faccao_id, "p_include_co": include_co, "p_max_pairs": max_pairs}
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    timeout = httpx.Timeout(int(os.getenv("SUPABASE_TIMEOUT", "15")))
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code != 200:
            raise HTTPException(
                status_code=502, detail=f"Supabase RPC falhou ({r.status_code}): {r.text}"
            )
        try:
            return r.json()
        except Exception as ex:
            raise HTTPException(status_code=502, detail=f"Supabase retorno inválido: {ex}")


async def fetch_graph(
    faccao_id: Optional[int], include_co: bool, max_pairs: int, use_cache: bool
) -> Dict[str, Any]:
    cache_key = f"kg:raw:{faccao_id}:{int(include_co)}:{max_pairs}"
    if use_cache and redis_client is not None:
        val = redis_client.get(cache_key)
        if val:
            return json.loads(val)
    raw = await supabase_rpc(faccao_id, include_co, max_pairs)
    if use_cache and redis_client is not None:
        redis_client.setex(cache_key, CACHE_API_TTL, json.dumps(raw))
    return raw


async def fetch_graph_sanitized(
    faccao_id: Optional[int], include_co: bool, max_pairs: int, use_cache: bool
) -> Dict[str, Any]:
    return sanitize_graph(await fetch_graph(faccao_id, include_co, max_pairs, use_cache))


def csp_inline() -> str:
    return (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self';"
    )


# -----------------------------------------------------------------------------
# Health e home (sempre 200)
# -----------------------------------------------------------------------------
@app.get("/")
def root():
    return {"svc": "kg", "version": APP_VERSION, "env": APP_ENV}


@app.get("/health")
def health():
    return {"ok": True, "version": APP_VERSION}


@app.get("/live")
def live():
    return {"live": True}


@app.get("/ready")
async def ready():
    ok_redis = False
    if redis_client is not None:
        try:
            ok_redis = redis_client.ping()
        except Exception:
            ok_redis = False

    backend_ok = False
    err = None
    try:
        _ = await supabase_rpc(None, True, 1)
        backend_ok = True
    except Exception as e:
        err = str(e)

    status = 200 if backend_ok else 503
    return JSONResponse(
        {"redis": ok_redis, "backend": "supabase", "backend_ok": backend_ok, "error": err}, status
    )


# -----------------------------------------------------------------------------
# API JSON
# -----------------------------------------------------------------------------
@app.get("/v1/graph/membros")
async def graph_membros(
    faccao_id: Optional[int] = Query(default=None),
    include_co: bool = Query(default=True),
    max_pairs: int = Query(default=20000, ge=1, le=200000),
    max_nodes: int = Query(default=2000, ge=1, le=20000),
    max_edges: int = Query(default=4000, ge=1, le=200000),
    cache: bool = Query(default=True),
):
    data = await fetch_graph_sanitized(faccao_id, include_co, max_pairs, use_cache=cache)
    data = truncate_graph(data, max_nodes, max_edges)
    degree_based_size(data["nodes"], data["edges"])
    return JSONResponse(data)


# -----------------------------------------------------------------------------
# Visualização: PyVis
# -----------------------------------------------------------------------------
@app.get("/v1/vis/pyvis", response_class=HTMLResponse)
async def vis_pyvis(
    response: Response,
    faccao_id: Optional[int] = Query(default=None),
    include_co: bool = Query(default=True),
    max_pairs: int = Query(default=8000, ge=1, le=200000),
    max_nodes: int = Query(default=2000, ge=100, le=20000),
    max_edges: int = Query(default=4000, ge=100, le=200000),
    cache: bool = Query(default=True),
    theme: str = Query(default="light", pattern="^(light|dark)$"),
    title: str = "Knowledge Graph (PyVis)",
):
    from pyvis.network import Network

    data = await fetch_graph_sanitized(faccao_id, include_co, max_pairs, use_cache=cache)
    data = truncate_graph(data, max_nodes, max_edges)
    degree_based_size(data["nodes"], data["edges"])

    net = Network(
        height="100%",
        width="100%",
        bgcolor="#0b0f19" if theme == "dark" else "#ffffff",
        font_color="#e6eef9" if theme == "dark" else "#111111",
        cdn_resources="in_line",
        notebook=False,
        directed=True,
    )
    net.barnes_hut()
    net.show_buttons(filter_=["physics"])

    for n in data["nodes"]:
        label = n.get("label") or n["id"]
        group = str(n.get("group") or n.get("type") or "0")
        size = float(n.get("size") or 10)
        net.add_node(
            n["id"], label=label, title=f"<b>{label}</b><br>grupo: {group}", value=size, shape="dot"
        )

    for e in data["edges"]:
        w = float(e.get("weight") or 1.0)
        rel = e.get("relation") or ""
        net.add_edge(e["source"], e["target"], value=w, title=f"{rel} (w={w})" if rel else f"w={w}")

    html = net.generate_html(title=title)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:;"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return HTMLResponse(html)


# -----------------------------------------------------------------------------
# Visualização: vis-network (vis.js)
# -----------------------------------------------------------------------------
@app.get("/v1/vis/visjs", response_class=HTMLResponse)
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
):
    data = await fetch_graph_sanitized(faccao_id, include_co, max_pairs, use_cache=cache)
    data = truncate_graph(data, max_nodes, max_edges)
    degree_based_size(data["nodes"], data["edges"])
    json_str = json.dumps(data, ensure_ascii=False)

    js_href = "https://unpkg.com/vis-network@9.1.6/dist/vis-network.min.js"
    css_href = "https://unpkg.com/vis-network@9.1.6/styles/vis-network.min.css"
    bg = "#0b0f19" if theme == "dark" else "#ffffff"

    html = f"""<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="stylesheet" href="{css_href}">
  <link rel="stylesheet" href="/static/vis-style.css">
  <meta name="theme-color" content="{bg}">
  <style>html,body,#mynetwork{{height:100%;margin:0}}</style>
  <script id="__KG_DATA__" type="application/json">{json_str}</script>
</head>
<body data-theme="{theme}">
  <div class="kg-toolbar">
    <h4>{title}</h4>
    <button onclick="window.print()">Print</button>
    <button onclick="location.reload()">Reload</button>
  </div>
  <div id="mynetwork" style="height:90vh;width:100%"></div>
  <script src="{js_href}" crossorigin="anonymous"></script>
  <script>
  (function(){{
    const container = document.getElementById('mynetwork');
    let data;
    try {{ data = JSON.parse(document.getElementById('__KG_DATA__').textContent || '{{}}'); }}
    catch(e) {{ container.innerHTML = '<pre>'+String(e)+'</pre>'; return; }}

    const nodes = (data.nodes||[]).map(n=>({{
      id: String(n.id), label: String(n.label||n.id),
      value: Number(n.size||10), group: String(n.group||n.type||'0'), shape:'dot'
    }}));
    const edges = (data.edges||[]).map(e=>({{
      from: String(e.source), to: String(e.target),
      value: Number(e.weight||1), title: e.relation||''
    }}));

    if(!nodes.length){{
      container.innerHTML = '<div style="display:flex;height:100%;align-items:center;justify-content:center;opacity:.85">Nenhum dado para exibir (nodes=0).</div>';
      return;
    }}

    const options = {{
      interaction: {{ hover:true, dragNodes:true, dragView:true, zoomView:true, multiselect:true, navigationButtons:true }},
      manipulation: {{ enabled:false }},
      physics: {{
        enabled:true,
        stabilization: {{ enabled:true, iterations: 400 }},
        barnesHut: {{ gravitationalConstant:-8000, centralGravity:0.2, springLength:120, springConstant:0.04, avoidOverlap:0.2 }}
      }},
      nodes: {{ borderWidth:1 }},
      edges: {{ smooth:false, arrows: {{ to: {{ enabled:true }} }} }}
    }};
    const network = new vis.Network(container, {{nodes, edges}}, options);
    network.once('stabilizationIterationsDone', ()=>network.fit({{animation:{{duration:300}}}}));
    network.on('doubleClick', ()=>network.fit({{animation:{{duration:300}}}}));
  }})();
  </script>
</body>
</html>"""
    response.headers["Content-Security-Policy"] = csp_inline()
    response.headers["X-Content-Type-Options"] = "nosniff"
    return HTMLResponse(html)
