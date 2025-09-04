import os
import json
from typing import Optional, Dict, Any
from string import Template

import httpx
from fastapi import FastAPI, Response, Query, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# =========================
# Config
# =========================
APP_ENV = os.getenv("APP_ENV", "development")
PORT = int(os.getenv("PORT", "8080"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

CORS_ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*")
CORS_ALLOW_CREDENTIALS = os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true"
CORS_ALLOW_HEADERS = os.getenv("CORS_ALLOW_HEADERS", "Authorization,Content-Type")
CORS_ALLOW_METHODS = os.getenv("CORS_ALLOW_METHODS", "GET,POST,OPTIONS")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_RPC_FN = os.getenv("SUPABASE_RPC_FN", "get_graph_membros")
SUPABASE_TIMEOUT = float(os.getenv("SUPABASE_TIMEOUT", "15"))

CACHE_API_TTL = int(os.getenv("CACHE_API_TTL", "60"))

ENABLE_REDIS_CACHE = os.getenv("ENABLE_REDIS_CACHE", "false").lower() == "true"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# =========================
# Redis opcional
# =========================
REDIS = None
if ENABLE_REDIS_CACHE:
    try:
        import redis.asyncio as redis

        REDIS = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    except Exception:
        REDIS = None


async def cache_get(key: str) -> Optional[str]:
    if not REDIS:
        return None
    try:
        return await REDIS.get(key)
    except Exception:
        return None


async def cache_set(key: str, value: str, ttl: int) -> None:
    if not REDIS:
        return
    try:
        await REDIS.setex(key, ttl, value)
    except Exception:
        pass


# =========================
# App + CORS + Static
# =========================
app = FastAPI(
    title="svc-kg",
    version="1.7.12",
    docs_url="/docs",  # Swagger UI
    redoc_url="/docs/redoc",
    openapi_url="/docs/openapi.json",
)

# CORS
cors_origins = [o.strip() for o in (CORS_ALLOW_ORIGINS or "*").split(",")]
cors_methods = [m.strip() for m in (CORS_ALLOW_METHODS or "*").split(",")]
cors_headers = [h.strip() for h in (CORS_ALLOW_HEADERS or "*").split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=cors_methods,
    allow_headers=cors_headers,
)

if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


# =========================
# Supabase helpers
# =========================
async def call_supabase_graph(
    faccao_id: Optional[int], include_co: bool, max_pairs: int
) -> Dict[str, Any]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL/SUPABASE_KEY ausentes")
    if not SUPABASE_URL.startswith("http"):
        raise RuntimeError("SUPABASE_URL inválida")

    url = SUPABASE_URL.rstrip("/") + f"/rest/v1/rpc/{SUPABASE_RPC_FN}"
    payload = {
        "p_faccao_id": faccao_id,
        "p_include_co": include_co,
        "p_max_pairs": max_pairs,
    }
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=SUPABASE_TIMEOUT) as client:
        r = await client.post(url, headers=headers, json=payload)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as he:
            raise HTTPException(
                status_code=he.response.status_code,
                detail=f"Supabase RPC {SUPABASE_RPC_FN} falhou: {he.response.text}",
            )
        try:
            data = r.json()
        except json.JSONDecodeError as je:
            raise HTTPException(
                status_code=502, detail=f"Supabase retornou JSON inválido: {str(je)}"
            )

    # alguns setups retornam [{...}]
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        data = data[0]
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = {}

    if not isinstance(data, dict):
        data = {}

    data.setdefault("nodes", [])
    data.setdefault("edges", [])
    return data


def normalize_graph_labels(data: Dict[str, Any]) -> Dict[str, Any]:
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    def clean_label(lbl: Any) -> str:
        if lbl is None:
            return ""
        s = str(lbl).strip()
        # "{A,B}" -> "A, B"
        if len(s) >= 2 and s[0] == "{" and s[-1] == "}":
            inner = s[1:-1]
            if not inner:
                return ""
            parts = [x.strip().strip('"') for x in inner.split(",")]
            parts = [p for p in parts if p and p.lower() != "null"]
            return ", ".join(parts)
        return s

    out_nodes, ids_seen = [], set()
    for n in nodes:
        nid = str(n.get("id", "")).strip()
        if not nid or nid in ids_seen:
            continue
        ids_seen.add(nid)
        n["id"] = nid
        n["label"] = clean_label(n.get("label"))
        if "group" not in n:
            g = n.get("faccao_id") or n.get("type") or 0
            n["group"] = g
        if isinstance(n.get("size"), str):
            try:
                n["size"] = float(n["size"])
            except Exception:
                n["size"] = None
        out_nodes.append(n)

    out_edges = []
    for e in edges:
        s = e.get("source")
        t = e.get("target")
        if s is None or t is None:
            continue
        out_edges.append(
            {
                "source": str(s),
                "target": str(t),
                "weight": e.get("weight"),
                "relation": e.get("relation"),
            }
        )
    return {"nodes": out_nodes, "edges": out_edges}


def truncate_preview(
    data: Dict[str, Any], max_nodes: int, max_edges: int
) -> Dict[str, Any]:
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    if max_nodes and len(nodes) > max_nodes:
        keep_ids = set(str(n.get("id")) for n in nodes[:max_nodes])
        nodes = nodes[:max_nodes]
        edges = [
            e
            for e in edges
            if e.get("source") in keep_ids and e.get("target") in keep_ids
        ]
    if max_edges and len(edges) > max_edges:
        edges = edges[:max_edges]
    return {"nodes": nodes, "edges": edges}


async def fetch_graph_sanitized(
    faccao_id: Optional[int],
    include_co: bool,
    max_pairs: int,
    *,
    use_cache: bool = True,
) -> Dict[str, Any]:
    cache_key = None
    if use_cache and REDIS:
        cache_key = f"kg:v1:faccao={faccao_id}:co={int(include_co)}:pairs={max_pairs}"
        cached = await cache_get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except Exception:
                pass

    raw = await call_supabase_graph(faccao_id, include_co, max_pairs)
    norm = normalize_graph_labels(raw)

    if use_cache and cache_key:
        await cache_set(cache_key, json.dumps(norm), CACHE_API_TTL)
    return norm


# =========================
# Básicos
# =========================
@app.get("/health", response_class=PlainTextResponse, include_in_schema=False)
async def health():
    return PlainTextResponse("ok", status_code=200)


@app.get("/live", response_class=PlainTextResponse, include_in_schema=False)
async def live():
    return PlainTextResponse("ok", status_code=200)


@app.get("/ready", response_class=JSONResponse, include_in_schema=False)
async def ready():
    info = {"redis": bool(REDIS), "backend": "supabase", "backend_ok": False}
    if not SUPABASE_URL or not SUPABASE_URL.startswith("http"):
        info["error"] = "SUPABASE_URL inválida ou ausente"
        return JSONResponse(info, status_code=503)
    try:
        _ = await call_supabase_graph(None, False, 1)
        info["backend_ok"] = True
        return JSONResponse(info, status_code=200)
    except HTTPException as he:
        info["error"] = he.detail
        return JSONResponse(info, status_code=503)
    except Exception as e:
        info["error"] = str(e)
        return JSONResponse(info, status_code=503)


# OpenAPI em YAML
@app.get("/docs/openapi.yaml", include_in_schema=False)
async def openapi_yaml():
    try:
        import yaml

        text = yaml.safe_dump(app.openapi(), sort_keys=False, allow_unicode=True)
        return PlainTextResponse(text, media_type="application/yaml")
    except Exception:
        return JSONResponse(app.openapi())


# =========================
# API de dados
# =========================
@app.get(
    "/v1/graph/membros",
    response_class=JSONResponse,
    summary="Retorna o grafo (nodes/edges) a partir do Supabase RPC",
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


# =========================
# VIS: vis-network (cores especiais + arestas por relação)
# =========================
@app.get(
    "/v1/vis/visjs",
    response_class=HTMLResponse,
    summary="Visualização vis-network (server-embed + client fetch)",
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
    local_js = "static/vendor/vis-network/vis-network.min.js"
    local_css = "static/vendor/vis-network/vis-network.min.css"
    has_local = os.path.exists(local_js) and os.path.exists(local_css)

    if has_local:
        js_href = "/static/vendor/vis-network/vis-network.min.js"
        css_href = "/static/vendor/vis-network/vis-network.min.css"
        csp = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self' data:; connect-src 'self';"
        )
    else:
        js_href = "https://unpkg.com/vis-network@9.1.6/dist/vis-network.min.js"
        css_href = "https://unpkg.com/vis-network@9.1.6/styles/vis-network.min.css"
        csp = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://unpkg.com; "
            "script-src 'self' 'unsafe-inline' https://unpkg.com; "
            "img-src 'self' data:; font-src 'self' data:; connect-src 'self';"
        )

    embedded_block = ""
    if source == "server":
        data = await fetch_graph_sanitized(
            faccao_id, include_co, max_pairs, use_cache=cache
        )
        out = truncate_preview(data, max_nodes, max_edges)
        out = normalize_graph_labels(out)
        embedded_block = (
            '<script id="__KG_DATA__" type="application/json">'
            + json.dumps(out, ensure_ascii=False)
            + "</script>"
        )

    bg = "#0b0f19" if theme == "dark" else "#ffffff"

    html_tpl = Template(
        """<!doctype html>
<html lang="pt-br">
  <head>
    <meta charset="utf-8" />
    <meta http-equiv="x-ua-compatible" content="ie=edge" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>$title</title>
    <link rel="stylesheet" href="$css_href">
    <link rel="stylesheet" href="/static/vis-style.css">
    <meta name="theme-color" content="$bg">
    <style>
      html,body,#mynetwork { height:100%; margin:0 }
      .kg-toolbar { display:flex; gap:.5rem; align-items:center; padding:.5rem .75rem; border-bottom:1px solid #e5e7eb; }
      .kg-toolbar h4 { margin:0; font-size: 14px; font-weight:600; }
      .badge { padding:.15rem .45rem; border-radius:.4rem; background:#eab308; color:#111827; font-size:.75rem; }
      button { cursor:pointer; }
      body[data-theme="dark"] { background:$bg; color:#f3f4f6; }
    </style>
  </head>
  <body data-theme="$theme">
    <div class="kg-toolbar">
      <h4>$title</h4>
      <button id="btn-print" type="button" title="Imprimir">Print</button>
      <button id="btn-reload" type="button" title="Recarregar">Reload</button>
      <span id="badge" class="badge" style="display:__DEBUG__">debug</span>
    </div>
    <div id="mynetwork"
         style="height:90vh;width:100%;"
         data-endpoint="/v1/graph/membros"
         data-debug="__DEBUG_BOOL__"
         data-source="$source"></div>
    $embedded_block
    <script src="$js_href" crossorigin="anonymous"></script>
    <script>
    (function(){
      var container = document.getElementById('mynetwork');
      var src = container.getAttribute('data-source') || 'server';
      var debug = container.getAttribute('data-debug') === 'true';
      var endpoint = container.getAttribute('data-endpoint') || '/v1/graph/membros';

      var EDGE_COLORS = {
        "PERTENCE_A": "#10b981",
        "EXERCE": "#f59e0b",
        "FUNCAO_DA_FACCAO": "#6366f1",
        "CO_FACCAO": "#a78bfa",
        "CO_FUNCAO": "#f472b6",
        "_default": "#9ca3af"
      };

      function hashColor(s){
        s = String(s||''); var h=0; for (var i=0;i<s.length;i++){ h=(h<<5)-h+s.charCodeAt(i); h|=0; }
        var hue=Math.abs(h)%360; return 'hsl(' + hue + ',70%,50%)';
      }
      function isPgTextArray(s){ s=(s||'').trim(); return s.length>=2 && s[0]=='{' && s[s.length-1]=='}'; }
      function cleanLabel(raw){
        if(!raw) return '';
        var s=String(raw).trim();
        if(!isPgTextArray(s)) return s;
        var inner=s.slice(1,-1);
        if(!inner) return '';
        return inner
          .replace(/(^|,)\\s*\"?null\"?\\s*(?=,|$)/gi,'')
          .replace(/\"/g,'')
          .split(',').map(function(x){return x.trim();})
          .filter(Boolean).join(', ');
      }
      function colorForNode(n){
        var lbl = (cleanLabel(n.label)||'').toUpperCase();
        var group = String(n.group ?? n.type ?? '');
        if (lbl === 'CV' || group.toUpperCase() === 'CV') return '#e11d48';
        if (lbl.indexOf('PCC') !== -1 || group.toUpperCase() === 'PCC') return '#2563eb';
        return hashColor(group || '0');
      }
      function degreeMap(nodes,edges){
        var d={}; nodes.forEach(function(n){ d[n.id]=0; });
        edges.forEach(function(e){ if(d.hasOwnProperty(e.from)) d[e.from]++; if(d.hasOwnProperty(e.to)) d[e.to]++; });
        return d;
      }
      function attachToolbar(net,nc,ec){
        var p=document.getElementById('btn-print'); if(p) p.onclick=function(){ window.print(); };
        var r=document.getElementById('btn-reload'); if(r) r.onclick=function(){ location.reload(); };
        var b=document.getElementById('badge');
        if(b && debug){ b.textContent='nodes: ' + nc + ' · edges: ' + ec; b.style.display='inline-block'; }
        else if(b){ b.style.display='none'; }
      }

      function render(data){
        var nodes=(data.nodes||[]).filter(function(n){return n && n.id;}).map(function(n){
          var v = (typeof n.size==='number') ? n.size : undefined;
          var group = String(n.group ?? n.type ?? '0');
          return {
            id: String(n.id),
            label: cleanLabel(n.label)||String(n.id),
            group: group,
            value: v,
            color: colorForNode(n),
            shape: 'dot'
          };
        });

        var edges=(data.edges||[]).filter(function(e){return e && e.source && e.target;}).map(function(e){
          var w = (e.weight != null ? Number(e.weight) : 1.0);
          var rel = e.relation || '';
          var t = rel ? (rel + ' (w=' + w + ')') : ('w=' + w);
          var col = EDGE_COLORS[rel] || EDGE_COLORS._default;
          return { from:String(e.source), to:String(e.target), value:w, title:t, color:{ color: col, opacity: 0.75 } };
        });

        if(!nodes.length){
          container.innerHTML='<div style="display:flex;height:100%;align-items:center;justify-content:center;opacity:.85">Nenhum dado para exibir (nodes=0).</div>';
          return;
        }
        var anySize = nodes.some(function(n){ return typeof n.value==='number'; });
        if(!anySize){
          var deg=degreeMap(nodes,edges);
          nodes.forEach(function(n){ var d=deg[n.id]||0; n.value = 10 + Math.log(d+1) * 8; });
        }

        var dsNodes=new vis.DataSet(nodes);
        var dsEdges=new vis.DataSet(edges);
        var options={
          interaction:{ hover:true, dragNodes:true, dragView:true, zoomView:true, multiselect:true, navigationButtons:true },
          manipulation:{ enabled:false },
          physics:{
            enabled:true,
            stabilization:{ enabled:true, iterations:500 },
            barnesHut:{ gravitationalConstant:-8000, centralGravity:0.2, springLength:120, springConstant:0.04, avoidOverlap:0.2 }
          },
          nodes:{ borderWidth:1, shape:'dot' },
          edges:{ smooth:false, arrows:{ to:{enabled:true} } }
        };
        var net=new vis.Network(container, {nodes:dsNodes,edges:dsEdges}, options);
        net.once('stabilizationIterationsDone', function(){ net.fit({animation:{duration:300}}); });
        net.on('doubleClick', function(){ net.fit({animation:{duration:300}}); });
        attachToolbar(net, nodes.length, edges.length);
      }

      function run(){
        if(typeof vis==='undefined'){
          container.innerHTML='<div style="padding:12px">vis-network não carregou. Verifique CSP/CDN.</div>';
          return;
        }
        if(src==='server'){
          var tag=document.getElementById('__KG_DATA__');
          if(!tag){ container.innerHTML='<div style="padding:12px">Bloco de dados ausente.</div>'; return; }
          try { render(JSON.parse(tag.textContent||'{}')); }
          catch(e){ console.error(e); container.innerHTML='<pre>'+String(e)+'</pre>'; }
        } else {
          var params=new URLSearchParams(window.location.search);
          var qs=new URLSearchParams();
          var fac=params.get('faccao_id'); if(fac && fac.trim()!=='') qs.set('faccao_id',fac.trim());
          qs.set('include_co', params.get('include_co') || 'true');
          qs.set('max_pairs',  params.get('max_pairs')  || '8000');
          qs.set('max_nodes',  params.get('max_nodes')  || '2000');
          qs.set('max_edges',  params.get('max_edges')  || '4000');
          qs.set('cache',      params.get('cache')      || 'false');
          fetch(('/v1/graph/membros')+'?'+qs.toString(), { headers:{'Accept':'application/json'} })
            .then(function(r){ if(!r.ok) return r.text().then(function(t){ throw new Error(r.status+': '+t); }); return r.json(); })
            .then(render)
            .catch(function(err){ console.error(err); container.innerHTML='<pre>'+String(err).replace(/</g,'&lt;')+'</pre>'; });
        }
      }
      if(document.readyState!=='loading') run(); else document.addEventListener('DOMContentLoaded', run);
    })();
    </script>
  </body>
</html>"""
    )

    html = (
        html_tpl.safe_substitute(
            title=title,
            css_href=css_href,
            js_href=js_href,
            bg=bg,
            theme=theme,
            source=source,
            embedded_block=embedded_block,
        )
        .replace("__DEBUG__", "inline-block" if debug else "none")
        .replace("__DEBUG_BOOL__", "true" if debug else "false")
    )

    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self';"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return HTMLResponse(content=html, status_code=200)


# =========================
# VIS: PyVis (corrigido — ignora arestas órfãs e opções JSON puras)
# =========================
@app.get(
    "/v1/vis/pyvis",
    response_class=HTMLResponse,
    summary="Visualização pyvis (server-side render)",
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
    title: str = "Knowledge Graph (pyvis)",
    # aceito/ignorado (para compatibilidade com URLs usadas no visjs)
    debug: bool = Query(default=False),
    source: str = Query(default="server"),
):
    data = await fetch_graph_sanitized(
        faccao_id, include_co, max_pairs, use_cache=cache
    )
    data = truncate_preview(data, max_nodes, max_edges)
    data = normalize_graph_labels(data)

    from pyvis.network import Network

    net = Network(
        height="90vh",
        width="100%",
        bgcolor="#0b0f19" if theme == "dark" else "#ffffff",
        font_color="#f3f4f6" if theme == "dark" else "#111827",
        directed=True,
        notebook=False,
        cdn_resources="in_line",
    )

    def color_for_node(label: str, group: str) -> str:
        u = (label or "").upper()
        g = (group or "").upper()
        if u == "CV" or g == "CV":
            return "#e11d48"
        if "PCC" in u or g == "PCC":
            return "#2563eb"
        h = abs(hash(g or "0")) % 360
        return f"hsl({h},70%,50%)"

    EDGE_COLORS = {
        "PERTENCE_A": "#10b981",
        "EXERCE": "#f59e0b",
        "FUNCAO_DA_FACCAO": "#6366f1",
        "CO_FACCAO": "#a78bfa",
        "CO_FUNCAO": "#f472b6",
        "_default": "#9ca3af",
    }

    # ---- montar estruturas seguras
    nodes_raw = data.get("nodes", [])
    edges_raw = data.get("edges", [])

    node_ids = {str(n.get("id")).strip() for n in nodes_raw if str(n.get("id")).strip()}
    # manter apenas arestas que conectam nós existentes
    edges_safe = []
    deg = {nid: 0 for nid in node_ids}
    for e in edges_raw:
        s = str(e.get("source") or "").strip()
        t = str(e.get("target") or "").strip()
        if not s or not t or s not in node_ids or t not in node_ids:
            continue
        edges_safe.append(e)
        deg[s] += 1
        deg[t] += 1

    # adicionar nós (com tamanho calculado se não vier)
    for n in nodes_raw:
        nid = str(n.get("id") or "").strip()
        if nid not in node_ids:
            continue
        label = (n.get("label") or nid).strip()
        group = str(n.get("group", n.get("type", "0")))
        size = n.get("size")
        if not isinstance(size, (int, float)):
            size = 10 + (0 if deg.get(nid, 0) == 0 else (8 * (deg[nid] ** 0.5)))
        net.add_node(
            nid,
            label=label,
            group=group,
            value=float(size),
            color=color_for_node(label, group),
            shape="dot",
        )

    # adicionar arestas (já seguras)
    for e in edges_safe:
        s = str(e.get("source"))
        t = str(e.get("target"))
        w = e.get("weight", 1)
        rel = e.get("relation", "")
        title_e = (rel + f" (w={w})") if rel else f"w={w}"
        col = EDGE_COLORS.get(rel, EDGE_COLORS["_default"])
        net.add_edge(
            s,
            t,
            value=(float(w) if isinstance(w, (int, float)) else 1.0),
            title=title_e,
            color=col,
            arrows="to",
        )

    # PyVis espera JSON puro, não "var options = ..."
    net.set_options(
        """
    {
      "interaction": { "hover": true, "dragNodes": true, "dragView": true, "zoomView": true, "navigationButtons": true },
      "physics": {
        "enabled": true,
        "stabilization": { "enabled": true, "iterations": 500 },
        "barnesHut": { "gravitationalConstant": -8000, "centralGravity": 0.2, "springLength": 120, "springConstant": 0.04, "avoidOverlap": 0.2 }
      },
      "nodes": { "shape": "dot", "borderWidth": 1 },
      "edges": { "smooth": false, "arrows": { "to": { "enabled": true } } }
    }
    """
    )

    html_inner = net.generate_html(notebook=False)
    toolbar = (
        '<div class="kg-toolbar" '
        'style="display:flex;gap:.5rem;align-items:center;padding:.5rem .75rem;border-bottom:1px solid #e5e7eb;">'
        f'<h4 style="margin:0;font-size:14px;font-weight:600;">{title}</h4>'
        '<button id="btn-print" type="button">Print</button>'
        '<button id="btn-reload" type="button">Reload</button>'
        "</div>"
        '<script>document.getElementById("btn-print").onclick=function(){window.print();};'
        'document.getElementById("btn-reload").onclick=function(){location.reload();};</script>'
    )
    html_final = (
        html_inner.replace("<body>", "<body>" + toolbar, 1)
        if "<body>" in html_inner
        else "<!doctype html><html><head><meta charset='utf-8'><title>"
        + title
        + "</title></head><body>"
        + toolbar
        + html_inner
        + "</body></html>"
    )

    response.headers["Content-Security-Policy"] = (
        "default-src 'self' blob: data:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self';"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return HTMLResponse(content=html_final, status_code=200)


# =========================
# Main
# =========================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=PORT, log_level=LOG_LEVEL)
