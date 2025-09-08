# =============================================================================
# Arquivo: app.py
# Versão: v1.7.20
# Objetivo: API FastAPI do micro-serviço svc-kg (graph + visualizações + ops)
# Funções/métodos:
# - live/health/ready/ops_status: sondas e status operacional
# - graph_membros: retorna grafo (nós/arestas) via Supabase RPC (fallback com e sem p_)
# - vis_visjs: página HTML com vis-network (sem f-string no JS; arestas ultrafinas; busca; cores CV/PCC/funções; física OFF após estabilizar)
# - vis_pyvis: página HTML com PyVis (arestas ultrafinas; física OFF após estabilizar; busca)
# - /docs: Swagger UI custom usando /openapi.json do FastAPI
# - Utilidades: normalização de labels PG array, cache Redis, truncamento seguro
# Atualização: 08/09/2025 17h51min
# =============================================================================

import os
import json
import logging
import socket
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Query, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

try:
    from redis import asyncio as aioredis  # redis 5.x
except Exception:  # pragma: no cover
    aioredis = None

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
APP_ENV = os.getenv("APP_ENV", "production")
LOG_LEVEL = os.getenv("LOG_LEVEL", "info").upper()

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip()
SUPABASE_SERVICE_KEY = (
    os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    or os.getenv("SUPABASE_KEY", "").strip()
    or os.getenv("SUPABASE_ANON_KEY", "").strip()
)
SUPABASE_RPC_FN = os.getenv("SUPABASE_RPC_FN", "get_graph_membros")
SUPABASE_TIMEOUT = float(os.getenv("SUPABASE_TIMEOUT", "15"))

ENABLE_REDIS_CACHE = os.getenv("ENABLE_REDIS_CACHE", "false").lower() == "true"
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CACHE_API_TTL = int(os.getenv("CACHE_API_TTL", "60"))
CACHE_STATIC_MAX_AGE = int(os.getenv("CACHE_STATIC_MAX_AGE", "86400"))

# -----------------------------------------------------------------------------
# App / Logger / CORS
# -----------------------------------------------------------------------------
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
log = logging.getLogger("svc-kg")

app = FastAPI(
    title="svc-kg",
    version="v1.7.20",
    description="Micro serviço de Knowledge Graph com visualizações (vis.js e PyVis).",
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
)

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
if os.path.isdir("docs"):
    app.mount("/docs-static", StaticFiles(directory="docs"), name="docs-static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=(
        ["*"]
        if os.getenv("CORS_ALLOW_ORIGINS", "*") == "*"
        else [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")]
    ),
    allow_credentials=(os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true"),
    allow_methods=[
        m.strip()
        for m in os.getenv("CORS_ALLOW_METHODS", "GET,POST,OPTIONS").split(",")
    ],
    allow_headers=[
        h.strip()
        for h in os.getenv("CORS_ALLOW_HEADERS", "Authorization,Content-Type").split(
            ","
        )
    ],
)

# -----------------------------------------------------------------------------
# Helpers (HTTP/Redis)
# -----------------------------------------------------------------------------
_http: Optional[httpx.AsyncClient] = None
_redis = None  # type: ignore


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
            content = fh.read()
        return ("docker" in content) or ("kubepods" in content)
    except Exception:
        return False


def platform_info() -> Dict[str, Any]:
    return {
        "hostname": socket.gethostname(),
        "container": running_in_container(),
        "coolify_proxy_network": os.getenv("COOLIFY_PROXY_NETWORK") or None,
        "app_env": APP_ENV,
        "service_id": "svc-kg",
        "aka": ["sic-kg"],
        "version": app.version,
    }


# -----------------------------------------------------------------------------
# Utils: normalização e truncamento
# -----------------------------------------------------------------------------
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

    fixed_nodes: List[Dict[str, Any]] = []
    node_ids: set = set()
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

    fixed_edges: List[Dict[str, Any]] = []
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


# -----------------------------------------------------------------------------
# Backend (Supabase RPC) com fallback
# -----------------------------------------------------------------------------
async def _rpc_call(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/rpc/{SUPABASE_RPC_FN}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    client = await _get_http()
    resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"{resp.status_code}: {resp.text}")
    return resp.json()


async def supabase_rpc_get_graph(
    faccao_id: Optional[int], include_co: bool, max_pairs: int
) -> Dict[str, Any]:
    if not _env_backend_ok():
        raise RuntimeError(
            "backend_not_configured: defina SUPABASE_URL/SUPABASE_SERVICE_KEY"
        )
    try:
        data = await _rpc_call(
            {"faccao_id": faccao_id, "include_co": include_co, "max_pairs": max_pairs}
        )
    except Exception as e1:
        msg = str(e1)
        # Fallback para versões do RPC que usam parâmetros prefixados com p_
        if "PGRST202" not in msg and "404" not in msg:
            raise RuntimeError(f"Supabase RPC {SUPABASE_RPC_FN} falhou: {msg}")
        data = await _rpc_call(
            {
                "p_faccao_id": faccao_id,
                "p_include_co": include_co,
                "p_max_pairs": max_pairs,
            }
        )

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
    return data


async def fetch_graph_sanitized(
    faccao_id: Optional[int], include_co: bool, max_pairs: int, use_cache: bool = True
) -> Dict[str, Any]:
    cache_key = f"kg:graph:{faccao_id}:{include_co}:{max_pairs}"
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
            await r.set(
                cache_key, json.dumps(fixed, ensure_ascii=False), ex=CACHE_API_TTL
            )
    return fixed


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
        await _redis.close()  # type: ignore
        _redis = None


# -----------------------------------------------------------------------------
# Ops
# -----------------------------------------------------------------------------
@app.get("/live", response_class=PlainTextResponse, tags=["ops"])
async def live():
    return PlainTextResponse("ok", status_code=200)


@app.get("/health", response_class=JSONResponse, tags=["ops"])
async def health(deep: bool = Query(default=False)):
    out = platform_info()
    out.update(
        {
            "status": "ok",
            "redis": False,
            "backend": "supabase" if _env_backend_ok() else "none",
        }
    )
    r_ok = True
    r = await _get_redis()
    if r:
        try:
            out["redis"] = bool(await r.ping())
        except Exception as e:
            r_ok = False
            out["redis_error"] = str(e)
    b_ok = _env_backend_ok()
    if deep and b_ok:
        try:
            _ = await supabase_rpc_get_graph(None, False, 1)
        except Exception as e:
            b_ok = False
            out["backend_error"] = str(e)
    out["ok"] = (not ENABLE_REDIS_CACHE or r_ok) and b_ok
    out["supabase"] = {
        "url": SUPABASE_URL,
        "rpc_fn": SUPABASE_RPC_FN,
        "timeout": SUPABASE_TIMEOUT,
        "service_key_tail": redact(SUPABASE_SERVICE_KEY),
    }
    return JSONResponse(out, status_code=200 if out["ok"] else 503)


@app.get("/ready", response_class=JSONResponse, tags=["ops"])
async def ready():
    r_ok = True
    out = platform_info()
    r = await _get_redis()
    if r:
        try:
            out["redis"] = bool(await r.ping())
        except Exception as e:
            r_ok = False
            out["redis_error"] = str(e)

    b_ok = False
    if _env_backend_ok():
        try:
            _ = await supabase_rpc_get_graph(None, False, 1)
            b_ok = True
        except Exception as e:
            out["backend_error"] = str(e)

    out["ok"] = (not ENABLE_REDIS_CACHE or r_ok) and b_ok
    return JSONResponse(out, status_code=200 if out["ok"] else 503)


@app.get("/ops/status", response_class=JSONResponse, tags=["ops"])
async def ops_status():
    info = platform_info()
    redis_cfg = {"enabled": ENABLE_REDIS_CACHE, "url": REDIS_URL}
    if ENABLE_REDIS_CACHE and aioredis:
        try:
            r = await _get_redis()
            if r:
                redis_cfg["ping"] = bool(await r.ping())
        except Exception as e:
            redis_cfg["error"] = str(e)
    supa = {
        "configured": _env_backend_ok(),
        "url": SUPABASE_URL,
        "rpc_fn": SUPABASE_RPC_FN,
        "timeout": SUPABASE_TIMEOUT,
        "service_key_tail": redact(SUPABASE_SERVICE_KEY),
    }
    info.update({"redis": redis_cfg, "supabase": supa})
    return JSONResponse(info, status_code=200)


# -----------------------------------------------------------------------------
# API de dados do grafo
# -----------------------------------------------------------------------------
@app.get("/v1/graph/membros", response_class=JSONResponse, tags=["graph"])
async def graph_membros(
    faccao_id: Optional[int] = Query(default=None),
    include_co: bool = Query(default=True),
    max_pairs: int = Query(default=8000, ge=1, le=200000),
    max_nodes: int = Query(default=2000, ge=50, le=20000),
    max_edges: int = Query(default=4000, ge=50, le=200000),
    cache: bool = Query(default=True),
):
    try:
        data = await fetch_graph_sanitized(
            faccao_id, include_co, max_pairs, use_cache=cache
        )
        data = truncate_preview(data, max_nodes, max_edges)
        return JSONResponse(data, status_code=200)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"graph_fetch_error: {e}")


# -----------------------------------------------------------------------------
# VIS.JS (vis-network) — sem f-string ao redor do JS para evitar problemas com chaves
# -----------------------------------------------------------------------------
@app.get("/v1/vis/visjs", response_class=HTMLResponse, tags=["viz"])
async def vis_visjs(
    response: Response,
    faccao_id: Optional[int] = Query(default=None),
    include_co: bool = Query(default=True),
    max_pairs: int = Query(default=8000),
    max_nodes: int = Query(default=2000),
    max_edges: int = Query(default=4000),
    cache: bool = Query(default=True),
    theme: str = Query(default="light"),
    title: str = Query(default="Knowledge Graph (vis.js)"),
    debug: bool = Query(default=False),
    source: str = Query(default="server", pattern="^(server|client)$"),
):
    embedded_block = ""
    if source == "server":
        try:
            data = await fetch_graph_sanitized(
                faccao_id, include_co, max_pairs, use_cache=cache
            )
            data = truncate_preview(data, max_nodes, max_edges)
            data = normalize_graph_labels(data)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"graph_fetch_error: {e}")
        embedded_block = (
            '<script id="__KG_DATA__" type="application/json">'
            + json.dumps(data, ensure_ascii=False)
            + "</script>"
        )

    js_href = (
        "/static/vendor/vis-network.min.js"
        if os.path.exists("static/vendor/vis-network.min.js")
        else "https://unpkg.com/vis-network@9.1.6/dist/vis-network.min.js"
    )
    css_href = (
        "/static/vendor/vis-network.min.css"
        if os.path.exists("static/vendor/vis-network.min.css")
        else "https://unpkg.com/vis-network@9.1.6/styles/vis-network.min.css"
    )
    bg = "#0b0f19" if theme == "dark" else "#ffffff"

    # ---- JavaScript embutido (NÃO É f-string) ----
    script_js = """
<script>
(function(){
  const container = document.getElementById('mynetwork');
  const source = container.getAttribute('data-source') || 'server';
  const endpoint = container.getAttribute('data-endpoint') || '/v1/graph/membros';

  const COLOR_CV  = '#d32f2f';  // vermelho
  const COLOR_PCC = '#0d47a1';  // azul escuro
  const COLOR_FUN = '#fdd835';  // amarelo (função)
  const COLOR_DEF = '#607d8b';  // cinza padrão

  const EDGE_COLORS = {
    'PERTENCE_A':       '#9e9e9e',
    'EXERCE':           COLOR_FUN,
    'FUNCAO_DA_FACCAO': COLOR_FUN,
    'CO_FACCAO':        '#aa9424',
    'CO_FUNCAO':        '#546e7a'
  };

  function isPgTextArray(s) { s=(s||'').trim(); return s.length>=2 && s[0]=='{' && s[s.length-1]=='}'; }
  function cleanLabel(raw) {
    if(!raw) return '';
    const s=String(raw).trim();
    if(!isPgTextArray(s)) return s;
    const inner=s.slice(1,-1); if(!inner) return '';
    return inner.replace(/(^|,)\\s*"?null"?\\s*(?=,|$)/gi,'').replace(/"/g,'').split(',').map(x=>x.trim()).filter(Boolean).join(', ');
  }

  // mapeia cor da facção pelo ID do nó de facção
  function inferFaccaoColors(rawNodes) {
    const map={};
    rawNodes.filter(n=>n && String(n.type||'').toLowerCase().includes('facc')).forEach(n=>{
      const name = cleanLabel(n.label||'').toUpperCase();
      const id = String(n.id);
      if (name.includes('PCC')) map[id] = COLOR_PCC;
      if (name.includes('CV'))  map[id] = COLOR_CV;
    });
    return map;
  }

  // regra de cor por nó (considera group/faccao_id; type; e o próprio label)
  function colorForNode(n, faccaoColorById) {
    const gid = String(n.group ?? n.faccao_id ?? '');
    if (gid && faccaoColorById[gid]) return faccaoColorById[gid];

    const t = String(n.type||'').toLowerCase();
    const L = String(n.label||'').toUpperCase();

    // variações "funcao/função"
    if (t.includes('funç') || t === 'funcao') return COLOR_FUN;

    // variações "faccao/facção"
    if (t.includes('facc')) {
      if (L.includes('CV'))  return COLOR_CV;
      if (L.includes('PCC')) return COLOR_PCC;
    }

    // fallback pelo rótulo do nó
    if (L.includes('CV'))  return COLOR_CV;
    if (L.includes('PCC')) return COLOR_PCC;

    return COLOR_DEF;
  }

  function edgeStyleFor(rel) { return { color: EDGE_COLORS[rel] || '#b0bec5', width: 0.1 }; } // ultrafina

  function degreeMap(nodes,edges) {
    const d={}; nodes.forEach(n=>d[n.id]=0);
    edges.forEach(e=>{ if(e.from in d) d[e.from]++; if(e.to in d) d[e.to]++; });
    return d;
  }

  function colorObj(c, opacity){
    if (typeof c === 'object' && c) { return Object.assign({}, c, { opacity: opacity }); }
    return {
      background: c || COLOR_DEF,
      border: c || COLOR_DEF,
      highlight: { background: c || COLOR_DEF, border: c || COLOR_DEF },
      hover: { background: c || COLOR_DEF, border: c || COLOR_DEF },
      opacity: opacity
    };
  }

  function render(data){
    const rawNodes = data.nodes || [];
    const rawEdges = data.edges || [];

    const faccaoColorById = inferFaccaoColors(rawNodes);

    // índice label por id (para colorir arestas por CV/PCC)
    const labelById = {};
    rawNodes.forEach(n => { labelById[String(n.id)] = cleanLabel(n.label||''); });

    // nós
    const nodes = [];
    const seen = new Set();
    for (const n of rawNodes) {
      if(!n || n.id==null) continue;
      const id = String(n.id);
      if (seen.has(id)) continue; seen.add(id);

      const label = cleanLabel(n.label) || id;
      const group = String(n.group ?? n.faccao_id ?? n.type ?? '0');
      const photo = n.photo_url && /^https?:\\/\\//i.test(n.photo_url) ? n.photo_url : null;

      const color = colorForNode({group, type:n.type, label}, faccaoColorById);

      const base = { id, label, group, color, borderWidth: 1 };
      if (photo) { base.shape='circularImage'; base.image=photo; } else { base.shape='dot'; }
      nodes.push(base);
    }

    const nodeIds = new Set(nodes.map(n=>n.id));

    // arestas (com cor puxada pelo label dos nós CV/PCC)
    const edges = [];
    for (const e of (rawEdges||[])) {
      if(!e) continue;
      const a = String(e.source ?? e.from);
      const b = String(e.target ?? e.to);
      if(!nodeIds.has(a) || !nodeIds.has(b)) continue;

      const rel = e.relation || '';
      const style = edgeStyleFor(rel);

      const la = String(labelById[a] || '').toUpperCase();
      const lb = String(labelById[b] || '').toUpperCase();

      let edgeColor = style.color;
      if (la.includes('CV') || lb.includes('CV')) edgeColor = COLOR_CV;
      else if (la.includes('PCC') || lb.includes('PCC')) edgeColor = COLOR_PCC;
      // funções continuam amarelas
      if (rel === 'EXERCE' || rel === 'FUNCAO_DA_FACCAO') edgeColor = COLOR_FUN;

      edges.push({ from:a, to:b, value: Number(e.weight||1), width: 0.1, color: edgeColor, title: rel });
    }

    if (!nodes.length) {
      container.innerHTML='<div style="padding:12px">Sem dados.</div>';
      return;
    }

    // explode nós com maior grau
    const deg = degreeMap(nodes, edges);
    nodes.forEach(n=>{ const d=deg[n.id]||0; n.value = 14 + Math.log(d+1)*10; });

    const dsNodes = new vis.DataSet(nodes);
    const dsEdges = new vis.DataSet(edges);

    const options = {
      interaction: { hover:true, dragNodes:true, dragView:true, zoomView:true, multiselect:true, navigationButtons:true },
      physics: { enabled: true, stabilization: { enabled:true, iterations: 300 } },
      nodes: { shape:'dot', borderWidth:2 },
      edges: { smooth:false, width:0.1, arrows: { to: { enabled: true, scaleFactor:0.5 } } }
    };

    const net = new vis.Network(container, { nodes: dsNodes, edges: dsEdges }, options);
    net.once('stabilizationIterationsDone', ()=>{ net.setOptions({ physics:false }); net.fit({ animation: { duration: 300 } }); });
    net.on('doubleClick', ()=> net.fit({ animation: { duration: 300 } }));

    // Busca/destaque
    const q = document.getElementById('kg-search');
    if (q) {
      function run() {
        const t=(q.value||'').trim().toLowerCase(); if(!t) return;
        const all=dsNodes.get();
        const hits=all.filter(n => (n.label||'').toLowerCase().includes(t) || String(n.id)===t);
        if(!hits.length) return;
        all.forEach(n => dsNodes.update({ id: n.id, color: colorObj(n.color, 0.25) }));
        hits.forEach(h => {
          const cur=dsNodes.get(h.id);
          dsNodes.update({ id: h.id, color: colorObj(cur.color, 1) });
        });
        net.setOptions({ physics: false });
        net.fit({ nodes: hits.map(h=>h.id), animation: { duration: 300 } });
      }
      q.addEventListener('change', run);
      q.addEventListener('keyup', e=>{ if(e.key==='Enter') run(); });
    }
    const p=document.getElementById('btn-print'); if(p) p.onclick=()=>window.print();
    const r=document.getElementById('btn-reload'); if(r) r.onclick=()=>location.reload();
  }

  function run(){
    if ((container.getAttribute('data-source')||'server') === 'server'){
      const tag=document.getElementById('__KG_DATA__'); if(!tag) { container.innerHTML='<div style="padding:12px">Dados não incorporados.</div>'; return; }
      try { render(JSON.parse(tag.textContent||'{}')); } catch(e){ container.innerHTML='<pre>'+String(e)+'</pre>'; }
    } else {
      const params=new URLSearchParams(window.location.search);
      const qs=new URLSearchParams();
      if(params.get('faccao_id')) qs.set('faccao_id', params.get('faccao_id'));
      qs.set('include_co', params.get('include_co') ?? 'true');
      qs.set('max_pairs',  params.get('max_pairs')  ?? '8000');
      qs.set('max_nodes',  params.get('max_nodes')  ?? '2000');
      qs.set('max_edges',  params.get('max_edges')  ?? '4000');
      qs.set('cache',      params.get('cache')      ?? 'true');
      const url=endpoint+'?'+qs.toString();
      fetch(url,{ headers:{ 'Accept':'application/json' } })
        .then(r=>r.json())
        .then(render)
        .catch(err=>{ container.innerHTML='<pre>'+String(err)+'</pre>'; });
    }
  }
  if(document.readyState!=='loading') run(); else document.addEventListener('DOMContentLoaded', run);
})();
</script>
"""

    # ---- HTML montado por concatenação ----
    parts: List[str] = []
    parts.append("<!doctype html>\n")
    parts.append('<html lang="pt-br">\n')
    parts.append("  <head>\n")
    parts.append('    <meta charset="utf-8" />\n')
    parts.append(f"    <title>{title}</title>\n")
    parts.append(f'    <link rel="stylesheet" href="{css_href}">\n')
    parts.append('    <link rel="stylesheet" href="/static/vis-style.css">\n')
    parts.append(f'    <meta name="theme-color" content="{bg}">\n')
    parts.append("    <style>\n")
    parts.append("      html,body,#mynetwork { height:100%; margin:0; }\n")
    parts.append(
        "      .kg-toolbar { display:flex; gap:8px; align-items:center; padding:8px; border-bottom:1px solid #e0e0e0; }\n"
    )
    parts.append(
        '      .kg-toolbar input[type="search"] { flex: 1; min-width: 220px; padding:6px 10px; border-radius:1px; outline:none; }\n'
    )
    parts.append(
        "      .kg-toolbar button { padding:6px 10px; border:1px solid #e0e0e0; background:transparent; border-radius:1px; cursor:pointer; }\n"
    )
    parts.append("      .kg-toolbar button:hover { background: rgba(0,0,0,.04); }\n")
    parts.append("    </style>\n")
    parts.append("  </head>\n")
    parts.append(f'  <body data-theme="{theme}">\n')
    parts.append('    <div class="kg-toolbar">\n')
    parts.append(f'      <h4 style="margin:0">{title}</h4>\n')
    parts.append(
        '      <input id="kg-search" type="search" placeholder="Buscar no gráfico" />\n'
    )
    parts.append(
        '      <button id="btn-print" type="button" title="Imprimir">Imprimir</button>\n'
    )
    parts.append(
        '      <button id="btn-reload" type="button" title="Recarregar">Recarregar</button>\n'
    )
    parts.append("    </div>\n")
    parts.append('    <div id="mynetwork" style="height:90vh;width:100%;"\n')
    parts.append('         data-endpoint="/v1/graph/membros"\n')
    parts.append(f'         data-source="{source}"\n')
    parts.append(f'         data-debug="{str(debug).lower()}"></div>\n')
    parts.append(f"    {embedded_block}\n")
    parts.append(f'    <script src="{js_href}"></script>\n')
    parts.append(script_js)
    parts.append("  </body>\n")
    parts.append("</html>\n")
    html = "".join(parts)

    # CSP: permitir imagens http/https (para photo_url)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' data: https: http:; "
        "connect-src 'self';"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return HTMLResponse(html, status_code=200)
