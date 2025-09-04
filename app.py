import os
import json
import math
import hashlib
from typing import Optional, Dict, Any, List, Tuple

import httpx
from fastapi import FastAPI, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from starlette.staticfiles import StaticFiles
import orjson

# =========================
# Config / ENV
# =========================
APP_ENV = os.getenv("APP_ENV", "production")
PORT = int(os.getenv("PORT", "8080"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

CORS_ALLOW_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
]
CORS_ALLOW_METHODS = [
    m.strip() for m in os.getenv("CORS_ALLOW_METHODS", "GET,POST,OPTIONS").split(",")
]
CORS_ALLOW_HEADERS = [
    h.strip()
    for h in os.getenv("CORS_ALLOW_HEADERS", "Authorization,Content-Type").split(",")
]
CORS_ALLOW_CREDENTIALS = os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true"

ENABLE_REDIS_CACHE = os.getenv("ENABLE_REDIS_CACHE", "false").lower() == "true"
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CACHE_API_TTL = int(os.getenv("CACHE_API_TTL", "60"))

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
SUPABASE_RPC_FN = os.getenv("SUPABASE_RPC_FN", "get_graph_membros")
SUPABASE_TIMEOUT = int(os.getenv("SUPABASE_TIMEOUT", "15"))

MEMBERS_TABLE = os.getenv("MEMBERS_TABLE", "membros")
MEMBERS_ID_COL = os.getenv("MEMBERS_ID_COL", "id")
MEMBERS_PHOTO_COL = os.getenv("MEMBERS_PHOTO_COL", "photo_url")  # ou "foto_path"

# =========================
# App
# =========================
app = FastAPI(title="svc-kg", version="1.7.11", docs_url="/docs", redoc_url="/redoc")
app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS if CORS_ALLOW_ORIGINS != ["*"] else ["*"],
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=CORS_ALLOW_METHODS,
    allow_headers=CORS_ALLOW_HEADERS,
)

# =========================
# Clients
# =========================
HTTP_CLIENT = httpx.AsyncClient(timeout=SUPABASE_TIMEOUT)

REDIS = None
if ENABLE_REDIS_CACHE:
    try:
        import redis.asyncio as aioredis

        REDIS = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    except Exception:
        REDIS = None


# =========================
# Utils
# =========================
def orjson_dumps(v, *, default):
    return orjson.dumps(v, default=default).decode()


def make_cache_key(prefix: str, params: Dict[str, Any]) -> str:
    h = hashlib.sha256(
        json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return f"{prefix}:{h}"


async def cache_get(key: str) -> Optional[Dict[str, Any]]:
    if not REDIS:
        return None
    val = await REDIS.get(key)
    return json.loads(val) if val else None


async def cache_set(key: str, value: Dict[str, Any], ttl: int = CACHE_API_TTL):
    if not REDIS:
        return
    await REDIS.setex(key, ttl, json.dumps(value, ensure_ascii=False))


def normalize_graph_labels(data: Dict[str, Any]) -> Dict[str, Any]:
    """Converte rótulos vindos como '{A,B}' / '{"X"}' em 'A, B' / 'X'."""

    def clean_label(raw):
        if raw is None:
            return ""
        s = str(raw).strip()
        if len(s) >= 2 and s[0] == "{" and s[-1] == "}":
            inner = s[1:-1]
            if not inner:
                return ""
            inner = inner.replace('"', "")
            parts = [
                p.strip()
                for p in inner.split(",")
                if p.strip().lower() != "null" and p.strip() != ""
            ]
            return ", ".join(parts)
        return s

    nodes = []
    for n in data.get("nodes", []):
        node = dict(n)
        node["label"] = clean_label(node.get("label"))
        nodes.append(node)

    return {"nodes": nodes, "edges": data.get("edges", [])}


def truncate_preview(
    data: Dict[str, Any], max_nodes: int, max_edges: int
) -> Dict[str, Any]:
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    if len(nodes) > max_nodes:
        keep_ids = {str(n["id"]) for n in nodes[:max_nodes]}
        nodes = nodes[:max_nodes]
        edges = [
            e
            for e in edges
            if str(e.get("source")) in keep_ids and str(e.get("target")) in keep_ids
        ]
    if len(edges) > max_edges:
        edges = edges[:max_edges]
    return {"nodes": nodes, "edges": edges}


async def fetch_graph_raw(
    faccao_id: Optional[int], include_co: bool, max_pairs: int
) -> Dict[str, Any]:
    if not SUPABASE_URL:
        return {"nodes": [], "edges": []}

    url = f"{SUPABASE_URL.rstrip('/')}/rpc/{SUPABASE_RPC_FN}"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    # Use Service key se existir; senão ANON
    token = SUPABASE_SERVICE_KEY or SUPABASE_ANON_KEY
    if token:
        headers["apikey"] = token
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "p_faccao_id": faccao_id,
        "p_include_co": include_co,
        "p_max_pairs": max_pairs,
    }

    r = await HTTP_CLIENT.post(url, headers=headers, json=payload)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and "nodes" in data and "edges" in data:
        return data
    # compat: algumas fns retornam já saneado
    return {"nodes": data.get("nodes", []), "edges": data.get("edges", [])}


async def fetch_graph_sanitized(
    faccao_id: Optional[int], include_co: bool, max_pairs: int, use_cache: bool = True
) -> Dict[str, Any]:
    cache_key = make_cache_key(
        "kg:graph",
        {"faccao_id": faccao_id, "include_co": include_co, "max_pairs": max_pairs},
    )
    if use_cache:
        hit = await cache_get(cache_key)
        if hit:
            return hit

    raw = await fetch_graph_raw(faccao_id, include_co, max_pairs)
    # saneia ids como string
    nodes = []
    seen = set()
    for n in raw.get("nodes", []):
        if not n:
            continue
        _id = str(n.get("id"))
        if not _id or _id in seen:
            continue
        seen.add(_id)
        nodes.append(
            {
                "id": _id,
                "label": n.get("label") or "",
                "type": n.get("type") or n.get("tipo") or "membro",
                "group": n.get("group") or n.get("faccao_id") or "",
                "size": n.get("size"),
                "photo_url": n.get("photo_url") or n.get("foto_path") or "",
            }
        )

    valid_ids = {n["id"] for n in nodes}
    edges = []
    for e in raw.get("edges", []):
        s = str(e.get("source"))
        t = str(e.get("target"))
        if not s or not t:
            continue
        if s not in valid_ids or t not in valid_ids:
            continue
        edges.append(
            {
                "source": s,
                "target": t,
                "weight": e.get("weight", 1.0),
                "relation": e.get("relation") or e.get("relacao") or "",
            }
        )

    out = {"nodes": nodes, "edges": edges}
    out = normalize_graph_labels(out)
    if use_cache:
        await cache_set(cache_key, out, CACHE_API_TTL)
    return out


def color_for_label(label: str) -> Optional[str]:
    """CV → vermelho, 'PCC' → azul-escuro; senão None (usa hash)."""
    if not label:
        return None
    L = label.upper()
    if "CV" == L or L.startswith("CV ") or " CV " in L or L.endswith(" CV"):
        return "#d62828"  # vermelho
    if "PCC" in L:
        return "#0d47a1"  # azul-escuro
    return None


def hash_color(s: str) -> str:
    h = hashlib.sha1(str(s).encode()).hexdigest()
    hue = int(h[:2], 16) % 360
    return f"hsl({hue},70%,50%)"


# =========================
# Health
# =========================
@app.get("/health", summary="Healthcheck simples")
async def health():
    return PlainTextResponse("ok", status_code=200)


@app.get("/live", summary="Liveness probe")
async def live():
    return PlainTextResponse("alive", status_code=200)


@app.get("/ready", summary="Readiness")
async def ready():
    status = {"redis": False, "backend": "supabase", "backend_ok": False}
    # redis
    if REDIS:
        try:
            pong = await REDIS.ping()
            status["redis"] = bool(pong)
        except Exception:
            status["redis"] = False
    # supabase
    try:
        _ = await fetch_graph_raw(None, True, 1)
        status["backend_ok"] = True
    except Exception as e:
        status["error"] = str(e)
    code = 200 if status["backend_ok"] else 503
    return JSONResponse(status, status_code=code)


# =========================
# API JSON
# =========================
@app.get("/v1/graph/membros", summary="Grafo (JSON)")
async def graph_membros(
    faccao_id: Optional[int] = Query(default=None),
    include_co: bool = Query(default=True),
    max_pairs: int = Query(default=8000, ge=1, le=200000),
    cache: bool = Query(default=True),
    max_nodes: int = Query(default=2000, ge=100, le=20000),
    max_edges: int = Query(default=4000, ge=100, le=200000),
):
    data = await fetch_graph_sanitized(
        faccao_id, include_co, max_pairs, use_cache=cache
    )
    data = truncate_preview(data, max_nodes, max_edges)
    return JSONResponse(data, dumps=orjson_dumps, media_type="application/json")


# =========================
# Templates helpers (sem f-string para evitar erros de { })
# =========================
def render_template(src: str, **kw) -> str:
    out = src
    for k, v in kw.items():
        out = out.replace(f"%%{k}%%", str(v))
    return out


# =========================
# VIS.JS VIEWER (com busca)
# =========================
@app.get(
    "/v1/vis/visjs",
    response_class=HTMLResponse,
    summary="Visualização vis-network (com busca, fotos e cores CV/PCC)",
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
    title: str = Query(default="Knowledge Graph (vis.js)"),
    debug: bool = Query(default=False),
    source: str = Query(default="server", pattern="^(server|client)$"),
):
    # Assets locais (sem depender de CDN)
    js_href = "/static/vendor/vis-network.min.js"
    css_href = "/static/vendor/vis-network.min.css"
    csp = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; img-src 'self' data: *; "
        "font-src 'self' data:; connect-src 'self';"
    )

    # Dados embutidos no HTML (source=server)
    embedded_block = ""
    if source == "server":
        data = await fetch_graph_sanitized(
            faccao_id, include_co, max_pairs, use_cache=cache
        )
        out = truncate_preview(data, max_nodes, max_edges)
        out = normalize_graph_labels(out)
        json_str = json.dumps(out, ensure_ascii=False)
        embedded_block = (
            f'<script id="__KG_DATA__" type="application/json">{json_str}</script>'
        )

    bg = "#0b0f19" if theme == "dark" else "#ffffff"

    html = r"""<!doctype html>
<html lang="pt-br">
  <head>
    <meta charset="utf-8" />
    <meta http-equiv="x-ua-compatible" content="ie=edge" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>%%TITLE%%</title>
    <link rel="stylesheet" href="%%CSS%%">
    <link rel="stylesheet" href="/static/vis-style.css">
    <meta name="theme-color" content="%%BG%%">
    <style>
      html,body,#mynetwork{height:100%;margin:0}
      .kg-toolbar{position:sticky;top:0;z-index:9;display:flex;gap:8px;align-items:center;padding:8px 10px;background:var(--bar-bg,#f7f7f7);border-bottom:1px solid #e3e3e3}
      .kg-toolbar h4{margin:0 8px 0 0;font:600 14px/1.2 system-ui}
      .kg-toolbar input[type="text"]{flex:1;min-width:200px;padding:6px 8px;border:1px solid #cfcfcf;border-radius:6px}
      .kg-toolbar button{padding:6px 10px;border:1px solid #cfcfcf;background:#fff;border-radius:6px;cursor:pointer}
      .badge{padding:2px 6px;border-radius:5px;background:#eee;border:1px solid #ddd;font:600 11px/1 system-ui}
      body[data-theme="dark"] .kg-toolbar{--bar-bg:#0e1525;border-color:#1e2633}
      body[data-theme="dark"] .kg-toolbar input, body[data-theme="dark"] .kg-toolbar button{background:#0f1828;color:#dfe7f5;border-color:#2a3242}
      .hint{opacity:.75;font-size:12px;margin-left:4px}
    </style>
  </head>
  <body data-theme="%%THEME%%">
    <div class="kg-toolbar">
      <h4>%%TITLE%%</h4>
      <input id="searchInput" type="text" placeholder="Buscar por ID ou nome..." />
      <button id="btn-search" type="button" title="Buscar">Buscar</button>
      <button id="btn-clear" type="button" title="Limpar busca">Limpar</button>
      <span class="hint">Arraste nós (o grafo não se move). Duplo clique = zoom/fit.</span>
      <span id="badge" class="badge" style="display:%%DBG_VIS%%">debug</span>
    </div>

    <div id="mynetwork"
         style="height:90vh;width:100%;"
         data-endpoint="/v1/graph/membros"
         data-debug="%%DEBUG%%"
         data-source="%%SOURCE%%"></div>

    %%EMBEDDED%%
    <script src="%%JS%%" crossorigin="anonymous"></script>
    <script>
    (function(){
      const container = document.getElementById('mynetwork');
      const source = container.getAttribute('data-source') || 'server';
      const debug = container.getAttribute('data-debug') === 'true';
      const endpoint = container.getAttribute('data-endpoint') || '/v1/graph/membros';
      // ---- helpers ----
      function isPgTextArray(s){ s=(s||'').trim(); return s.length>=2 && s[0]=='{' && s[s.length-1]=='}'; }
      function cleanLabel(raw){
        if(!raw) return '';
        const s=String(raw).trim();
        if(!isPgTextArray(s)) return s;
        const inner=s.slice(1,-1);
        if(!inner) return '';
        return inner.replace(/(^|,)\s*"?null"?\s*(?=,|$)/gi,'')
                    .replace(/"/g,'')
                    .split(',').map(x=>x.trim()).filter(Boolean).join(', ');
      }
      function colorRule(label, group){
        const L=(String(label||'').toUpperCase());
        if(L==='CV' || L.startsWith('CV ') || L.includes(' CV ') || L.endsWith(' CV')) return '#d62828';
        if(L.includes('PCC')) return '#0d47a1';
        // fallback hash:
        let s=String(group ?? label ?? '0'); let h=0; for (let i=0;i<s.length;i++){ h=(h<<5)-h+s.charCodeAt(i); h|=0; }
        const hue=Math.abs(h)%360; return `hsl(${hue},70%,50%)`;
      }
      function degreeMap(nodes,edges){
        const d={}; nodes.forEach(n=>d[n.id]=0);
        edges.forEach(e=>{ if(e.from in d) d[e.from]++; if(e.to in d) d[e.to]++; });
        return d;
      }
      function attachToolbar(net,dsNodes,dsEdges){
        const p=document.getElementById('btn-search');
        const c=document.getElementById('btn-clear');
        const i=document.getElementById('searchInput');
        const b=document.getElementById('badge');
        function runSearch(){
          const q=(i.value||'').trim().toLowerCase();
          // reset style
          dsNodes.forEach(n=>{ dsNodes.update({id:n.id, color:n.__baseColor, borderWidth:1}); });
          if(!q){ net.unselectAll(); return; }
          const all=dsNodes.get();
          const hits=all.filter(n=>{
            const lbl=(n.label||'').toLowerCase();
            return n.id.toLowerCase()===q || lbl.includes(q);
          });
          if(!hits.length){ return; }
          // highlight hits
          hits.forEach(n=> dsNodes.update({id:n.id, color:{background:'#ffd54f', border:'#ff6f00'}, borderWidth:2}));
          const ids=hits.map(h=>h.id);
          net.selectNodes(ids);
          // focus no primeiro
          net.focus(ids[0], {animation:{duration:300}, scale:1.2});
        }
        if(p) p.onclick=runSearch;
        if(i) i.addEventListener('keydown', e=>{ if(e.key==='Enter') runSearch(); });
        if(c) c.onclick=()=>{
          i.value='';
          dsNodes.forEach(n=>{ dsNodes.update({id:n.id, color:n.__baseColor, borderWidth:1}); });
          net.unselectAll();
          net.fit({animation:{duration:300}});
        };
        if(b && debug){ b.textContent=`nodes: ${dsNodes.length} · edges: ${dsEdges.length}`; b.style.display='inline-block'; }
      }
      function render(data){
        const nodes=(data.nodes||[]).filter(n=>n&&n.id).map(n=>{
          const label=cleanLabel(n.label)||String(n.id);
          const baseColor=colorRule(label, (n.group ?? n.type ?? '0'));
          const shape = n.photo_url ? 'circularImage' : 'dot';
          const image = n.photo_url || undefined;
          const value = (typeof n.size==='number') ? n.size : undefined;
          return { id:String(n.id), label, group:String(n.group ?? n.type ?? '0'), value, color:baseColor, __baseColor:baseColor, shape, image };
        });
        const edges=(data.edges||[]).filter(e=>e&&e.source&&e.target).map(e=>({
          from:String(e.source), to:String(e.target),
          value:(e.weight!=null? Number(e.weight):1.0),
          title: e.relation ? `${e.relation} (w=${e.weight ?? 1})` : `w=${e.weight ?? 1}`
        }));
        if(!nodes.length){
          container.innerHTML='<div style="display:flex;height:100%;align-items:center;justify-content:center;opacity:.85">Nenhum dado para exibir (nodes=0).</div>';
          return;
        }
        const hasSize = nodes.some(n=>typeof n.value==='number');
        if(!hasSize){
          const deg=degreeMap(nodes,edges);
          nodes.forEach(n=>{ const d=deg[n.id]||0; n.value=10+Math.log(d+1)*8; });
        }
        const dsNodes=new vis.DataSet(nodes);
        const dsEdges=new vis.DataSet(edges);
        const options={
          interaction:{ hover:true, dragNodes:true, dragView:false, zoomView:true, multiselect:true, navigationButtons:true },
          manipulation:{ enabled:false },
          physics:{ enabled:true, stabilization:{enabled:true, iterations:400},
                    barnesHut:{ gravitationalConstant:-8000, centralGravity:0.2, springLength:120, springConstant:0.04, avoidOverlap:0.2 } },
          nodes:{ borderWidth:1, shape:'dot' },
          edges:{ smooth:false, width:1, arrows:{ to:{enabled:true, scaleFactor:0.6} } }
        };
        const net=new vis.Network(container,{nodes:dsNodes, edges:dsEdges},options);
        net.once('stabilizationIterationsDone',()=>net.fit({animation:{duration:300}}));
        net.on('doubleClick',()=>net.fit({animation:{duration:300}}));
        attachToolbar(net,dsNodes,dsEdges);
      }
      function run(){
        if(typeof vis==='undefined'){
          container.innerHTML='<div style="padding:12px">vis-network não carregou.</div>';
          return;
        }
        if(source==='server'){
          const tag=document.getElementById('__KG_DATA__');
          if(!tag){ container.innerHTML='<div style="padding:12px">Bloco de dados ausente.</div>'; return; }
          try{ render(JSON.parse(tag.textContent||'{}')); }
          catch(e){ console.error(e); container.innerHTML='<pre>'+String(e)+'</pre>'; }
        }else{
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
            .catch(err=>{ console.error(err); container.innerHTML='<pre>'+String(err).replace(/</g,"&lt;")+'</pre>'; });
        }
      }
      if(document.readyState!=='loading') run(); else document.addEventListener('DOMContentLoaded', run);
    })();
    </script>
  </body>
</html>
"""
    html = render_template(
        html,
        TITLE=title,
        THEME=theme,
        BG=bg,
        JS=js_href,
        CSS=css_href,
        DEBUG=str(debug).lower(),
        SOURCE=source,
        EMBEDDED=embedded_block,
        DBG_VIS="inline-block" if debug else "none",
    )
    response.headers["Content-Security-Policy"] = csp
    response.headers["X-Content-Type-Options"] = "nosniff"
    return HTMLResponse(content=html, status_code=200)


# =========================
# PYVIS VIEWER (com busca)
# =========================
@app.get(
    "/v1/vis/pyvis",
    response_class=HTMLResponse,
    summary="Visualização PyVis (com busca, fotos e cores CV/PCC)",
)
async def vis_pyvis(
    response: Response,
    faccao_id: Optional[int] = Query(default=None),
    include_co: bool = Query(default=True),
    max_pairs: int = Query(default=8000, ge=1, le=200000),
    max_nodes: int = Query(default=2000, ge=100, le=20000),
    max_edges: int = Query(default=4000, ge=100, le=200000),
    cache: bool = Query(default=True),
    theme: str = Query(default="light", pattern="^(light|dark)$"),
    title: str = Query(default="Knowledge Graph (PyVis)"),
    debug: bool = Query(default=False),
):
    try:
        from pyvis.network import Network
    except Exception as e:
        return HTMLResponse(f"<pre>pyvis não disponível: {e}</pre>", status_code=500)

    # 1) Busca os dados
    data = await fetch_graph_sanitized(
        faccao_id, include_co, max_pairs, use_cache=cache
    )
    data = truncate_preview(data, max_nodes, max_edges)
    data = normalize_graph_labels(data)

    # 2) Monta o network PyVis
    height = "92vh"
    width = "100%"
    bgcolor = "#0b0f19" if theme == "dark" else "#ffffff"
    font_color = "#e6edf7" if theme == "dark" else "#222"

    net = Network(
        height=height,
        width=width,
        bgcolor=bgcolor,
        font_color=font_color,
        directed=True,
    )
    # opções mínimas para manter edges finas e drag do nó, sem arrastar o grafo
    net.set_options(
        """
    {
      "interaction": {"hover": true, "dragNodes": true, "dragView": false, "zoomView": true, "multiselect": true},
      "physics": {"enabled": true, "stabilization": {"enabled": true, "iterations": 400},
                  "barnesHut": {"gravitationalConstant": -8000, "centralGravity": 0.2, "springLength": 120, "springConstant": 0.04, "avoidOverlap": 0.2}},
      "edges": {"smooth": false, "width": 1, "arrows": {"to": {"enabled": true, "scaleFactor": 0.6}}},
      "nodes": {"borderWidth": 1}
    }
    """
    )

    # 3) Adiciona nós primeiro
    existing = set()
    for n in data.get("nodes", []):
        nid = str(n.get("id"))
        if not nid or nid in existing:
            continue
        existing.add(nid)

        label = n.get("label") or nid
        special = color_for_label(label)
        color = special or hash_color(n.get("group") or label)

        # foto circular se houver
        shape = "circularImage" if n.get("photo_url") else "dot"
        img = n.get("photo_url") or None

        size = n.get("size")
        if not isinstance(size, (int, float)) or math.isnan(size):
            size = None

        net.add_node(
            nid,
            label=label,
            title=label,
            color=(
                color if shape == "dot" else None
            ),  # circularImage não usa background do 'color'
            shape=shape,
            image=img,
            value=size,
        )

    # 4) Depois as arestas
    for e in data.get("edges", []):
        s = str(e.get("source"))
        t = str(e.get("target"))
        if s not in existing or t not in existing:
            continue
        w = e.get("weight", 1.0)
        rel = e.get("relation") or ""
        net.add_edge(
            s,
            t,
            value=float(w) if isinstance(w, (int, float)) else 1.0,
            title=f"{rel} (w={w})" if rel else f"w={w}",
        )

    # 5) Gera HTML e injeta barra de busca + script de interação
    html = net.generate_html(notebook=False)

    toolbar = r"""
<div class="kg-toolbar" style="position:sticky;top:0;z-index:9;display:flex;gap:8px;align-items:center;padding:8px 10px;background:var(--bar-bg,#f7f7f7);border-bottom:1px solid #e3e3e3">
  <h4 style="margin:0 8px 0 0;font:600 14px/1.2 system-ui">%%TITLE%%</h4>
  <input id="searchInput" type="text" placeholder="Buscar por ID ou nome..." style="flex:1;min-width:200px;padding:6px 8px;border:1px solid #cfcfcf;border-radius:6px" />
  <button id="btn-search" type="button" style="padding:6px 10px;border:1px solid #cfcfcf;background:#fff;border-radius:6px;cursor:pointer">Buscar</button>
  <button id="btn-clear" type="button" style="padding:6px 10px;border:1px solid #cfcfcf;background:#fff;border-radius:6px;cursor:pointer">Limpar</button>
  <span class="hint" style="opacity:.75;font-size:12px;margin-left:4px">Arraste nós (o grafo não se move). Duplo clique = zoom/fit.</span>
</div>
"""

    # injeta toolbar logo após <body> e antes do container
    html = html.replace("<body>", "<body>" + render_template(toolbar, TITLE=title))

    # injeta script de busca, foco e reset
    enhance = r"""
<script>
(function(){
  // PyVis expõe 'network', 'nodes', 'edges'
  function ensureReady(cb){
    if (typeof network !== 'undefined' && typeof nodes !== 'undefined' && typeof edges !== 'undefined') { cb(); return; }
    const iv = setInterval(function(){
      if (typeof network !== 'undefined' && typeof nodes !== 'undefined' && typeof edges !== 'undefined') {
        clearInterval(iv); cb();
      }
    }, 50);
  }
  ensureReady(function(){
    // aplica cores especiais em runtime (CV/PCC) + fotos já vieram como circularImage
    nodes.forEach(function(n){
      if(n.shape!=="circularImage"){
        var L = String(n.label||"").toUpperCase();
        if(L==="CV" || L.startsWith("CV ") || L.includes(" CV ") || L.endsWith(" CV")){
          nodes.update({id:n.id, color:"#d62828"});
        } else if(L.includes("PCC")){
          nodes.update({id:n.id, color:"#0d47a1"});
        }
      }
    });
    // impede arrastar o grafo (apenas nós)
    network.setOptions({interaction:{dragView:false}});
    // arestas finas
    network.setOptions({edges:{width:1}});
    // busca
    var i = document.getElementById('searchInput');
    var b = document.getElementById('btn-search');
    var c = document.getElementById('btn-clear');

    function runSearch(){
      if(!i) return;
      var q = (i.value||"").trim().toLowerCase();
      // reset visual
      nodes.forEach(function(n){ nodes.update({id:n.id, borderWidth:1}); });
      network.unselectAll();
      if(!q){ return; }
      var all = nodes.get();
      var hits = all.filter(function(n){
        var lbl = String(n.label||"").toLowerCase();
        return String(n.id).toLowerCase()===q || lbl.includes(q);
      });
      if(!hits.length) return;
      hits.forEach(function(n){ nodes.update({id:n.id, borderWidth:2}); });
      var ids = hits.map(function(n){ return n.id; });
      network.selectNodes(ids);
      network.focus(ids[0], {animation:{duration:300}, scale:1.2});
    }
    if(b) b.onclick = runSearch;
    if(i) i.addEventListener('keydown', function(e){ if(e.key==='Enter') runSearch(); });
    if(c) c.onclick = function(){
      if(i) i.value='';
      nodes.forEach(function(n){ nodes.update({id:n.id, borderWidth:1}); });
      network.unselectAll();
      network.fit({animation:{duration:300}});
    };
  });
})();
</script>
"""
    html = html.replace("</body>", enhance + "\n</body>")

    csp = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; img-src 'self' data: *; "
        "font-src 'self' data:; connect-src 'self';"
    )
    response.headers["Content-Security-Policy"] = csp
    response.headers["X-Content-Type-Options"] = "nosniff"
    return HTMLResponse(html, status_code=200)
