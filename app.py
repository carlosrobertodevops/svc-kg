# =============================================================================
# Arquivo: app.py
# Versão: v1.7.20
# Objetivo: API FastAPI do micro-serviço svc-kg (graph + visualizações + ops)
# Funções/métodos:
# - live/health/ready/ops_status: sondas e status operacional
# - graph_membros: retorna grafo (nós/arestas) via Supabase RPC (com fallback)
# - vis_visjs: HTML com vis-network (busca, cores CV/PCC/funções, arestas finas)
# - vis_pyvis: HTML com PyVis (arestas finas, física desligada após estabilizar, busca)
# - Utilidades: verificação de backend, normalização, deduplicação e “explosão” por grau
# =============================================================================

import os
import json
import math
import logging
from typing import Any, Dict, List, Tuple, Optional

import httpx
from fastapi import FastAPI, Query, Response, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse

logger = logging.getLogger("svc-kg")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

# -----------------------------------------------------------------------------
# FastAPI app + static + Swagger
# -----------------------------------------------------------------------------
app = FastAPI(
    title="svc-kg",
    version="v1.7.20",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# arquivos estáticos (JS/CSS)
app.mount("/static", StaticFiles(directory="static"), name="static")
# serve o diretório /docs estático em rota separada para não conflitar com Swagger
app.mount("/docs-static", StaticFiles(directory="docs"), name="docs-static")

# -----------------------------------------------------------------------------
# Config de backend (Supabase)
# -----------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
SUPABASE_RPC_FN = os.getenv("SUPABASE_RPC_FN", "get_graph_membros").strip()

# -----------------------------------------------------------------------------
# Helpers de ambiente/backend
# -----------------------------------------------------------------------------
def __env_backend_ok() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY and SUPABASE_RPC_FN)


def _http_headers_json() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }

# -----------------------------------------------------------------------------
# Fallback local – usado quando o RPC estiver indisponível
# -----------------------------------------------------------------------------
def _fallback_graph() -> Dict[str, Any]:
    nodes = [
        {"id": "6", "type": "faccao", "label": "CV"},
        {"id": "7", "type": "faccao", "label": "PCC"},
        {"id": "m1", "type": "membro", "label": "DEDE"},
        {"id": "m2", "type": "membro", "label": "BISCOITO"},
        {"id": "f1", "type": "funcao", "label": "Presidente"},
    ]
    edges = [
        {"source": "m1", "target": "6", "relation": "PERTENCE_A", "weight": 3},
        {"source": "m2", "target": "7", "relation": "PERTENCE_A", "weight": 3},
        {"source": "m1", "target": "f1", "relation": "EXERCE", "weight": 2},
    ]
    return {"nodes": nodes, "edges": edges}

# -----------------------------------------------------------------------------
# Supabase RPC (tenta com p_* e sem p_*)
# -----------------------------------------------------------------------------
async def supabase_rpc_get_graph(
    faccao_id: Optional[int],
    include_co: bool,
    max_pairs: int,
) -> Dict[str, Any]:
    if not __env_backend_ok():
        logger.warning("Backend não configurado. Usando fallback local.")
        return _fallback_graph()

    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/rpc/{SUPABASE_RPC_FN}"
    timeout = httpx.Timeout(15.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        # 1) assinatura com prefixo p_
        body_prefixed = {
            "p_faccao_id": faccao_id,
            "p_include_co": include_co,
            "p_max_pairs": max_pairs,
        }
        r = await client.post(url, headers=_http_headers_json(), json=body_prefixed)
        if r.status_code == 200:
            return r.json()

        # 2) assinatura sem prefixo (compatibilidade)
        body_plain = {
            "faccao_id": faccao_id,
            "include_co": include_co,
            "max_pairs": max_pairs,
        }
        r2 = await client.post(url, headers=_http_headers_json(), json=body_plain)
        if r2.status_code == 200:
            return r2.json()

        # 3) erro
        detail = r.text if r.text else r2.text
        msg = f"404: Supabase RPC {SUPABASE_RPC_FN} falhou: {detail}"
        logger.error(msg)
        raise HTTPException(status_code=404, detail=f"graph_fetch_error: {msg}")

# -----------------------------------------------------------------------------
# Sanitização para clientes (vis.js / PyVis) + dedup + “explosão” por grau
# -----------------------------------------------------------------------------
def _sanitize_graph_for_vis(raw: Dict[str, Any]) -> Dict[str, Any]:
    nodes_in = raw.get("nodes") or []
    edges_in = raw.get("edges") or []

    # nodes: padroniza e deduplica por id
    idx: Dict[str, Dict[str, Any]] = {}
    nodes_out: List[Dict[str, Any]] = []
    for n in nodes_in:
        nid = str(n.get("id"))
        if not nid or nid in idx:  # evita duplicados
            continue
        t = (n.get("type") or n.get("tipo") or "").lower() or "membro"
        label = (n.get("label") or n.get("nome") or str(nid)).strip()
        group = n.get("group")
        node_norm = {"id": nid, "type": t, "label": label, "group": group}
        nodes_out.append(node_norm)
        idx[nid] = node_norm

    # edges: converte source/target -> from/to, dedup por (from,to,relation)
    edges_tmp: List[Tuple[str, str, Dict[str, Any]]] = []
    deg: Dict[str, int] = {}
    for e in edges_in:
        _src = e.get("source") or e.get("from")
        _dst = e.get("target") or e.get("to")
        if _src is None or _dst is None:
            continue
        src = str(_src)
        dst = str(_dst)
        if src not in idx or dst not in idx:
            continue
        relation = (e.get("relation") or e.get("tipo") or "").upper()
        weight = e.get("weight") or 1
        edges_tmp.append((src, dst, {"relation": relation, "weight": weight}))
        deg[src] = deg.get(src, 0) + 1
        deg[dst] = deg.get(dst, 0) + 1

    # “explode” nós mais conectados (tamanho por log2 do grau)
    for n in nodes_out:
        d = deg.get(n["id"], 0)
        n["size"] = round(8.0 + math.log(d + 1.0, 2) * 6.0, 2)

    # monta edges finais já no formato vis-network
    edges_out: List[Dict[str, Any]] = []
    seen = set()
    for (src, dst, meta) in edges_tmp:
        key = (src, dst, meta["relation"])
        if key in seen:
            continue
        seen.add(key)
        rel = meta["relation"]
        directed = rel in ("PERTENCE_A", "EXERCE", "FUNCAO_DA_FACCAO")
        edges_out.append(
            {
                "from": src,
                "to": dst,
                "relation": rel,
                "value": float(meta["weight"] or 1),
                "arrows": "to" if directed else "",
            }
        )

    return {"nodes": nodes_out, "edges": edges_out}

# -----------------------------------------------------------------------------
# Sondas / status
# -----------------------------------------------------------------------------
@app.get("/live")
def live() -> Dict[str, str]:
    return {"status": "live"}

@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "backend": "supabase", "backend_ok": __env_backend_ok()}

@app.get("/ready")
def ready() -> Dict[str, Any]:
    return {"status": "ready", "backend": "supabase", "backend_ok": __env_backend_ok()}

@app.get("/ops/status")
def ops_status() -> Dict[str, Any]:
    return {
        "aka": ["sic-kg"],
        "service_id": "svc-kg",
        "version": "v1.7.20",
        "backend": "supabase",
        "backend_ok": __env_backend_ok(),
        "ok": True,
    }

# -----------------------------------------------------------------------------
# API JSON: grafo
# -----------------------------------------------------------------------------
@app.get("/v1/graph/membros")
async def graph_membros(
    faccao_id: Optional[int] = Query(None),
    include_co: bool = Query(True),
    max_pairs: int = Query(5000, ge=1, le=100000),
):
    try:
        raw = await supabase_rpc_get_graph(faccao_id, include_co, max_pairs)
        data = _sanitize_graph_for_vis(raw)
        return JSONResponse(data)
    except HTTPException:
        raise
    except Exception as ex:
        logger.exception("graph_membros erro inesperado")
        raise HTTPException(status_code=500, detail=f"graph_fetch_error: {ex}")

# -----------------------------------------------------------------------------
# VIS.JS (vis-network) – HTML
# -----------------------------------------------------------------------------
@app.get("/visjs")
async def vis_visjs(
    faccao_id: Optional[int] = Query(None),
    include_co: bool = Query(True),
    max_pairs: int = Query(5000, ge=1, le=100000),
    search: Optional[str] = Query(None),
):
    try:
        raw = await supabase_rpc_get_graph(faccao_id, include_co, max_pairs)
        data = _sanitize_graph_for_vis(raw)
        html = f"""<!doctype html>
<html lang="pt-br">
  <head>
    <meta charset="utf-8" />
    <title>Knowledge Graph (vis.js)</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link rel="stylesheet" href="/static/vendor/vis-network.min.css">
    <link rel="stylesheet" href="/static/vis-style.css">
    <style>html,body,#mynetwork{{height:100%;margin:0;background:#fff;}}</style>
  </head>
  <body data-theme="light">
    <div class="kg-toolbar">
      <h4>Knowledge Graph (vis.js)</h4>
      <input id="kg-search" type="search" placeholder="Buscar nó por rótulo ou ID…" />
      <button id="btn-fit" type="button">Ajustar</button>
      <button id="btn-reload" type="button" onclick="location.reload()">Recarregar</button>
    </div>

    <div id="mynetwork" style="height:calc(100vh - 56px);width:100%;" data-source="server"></div>

    <script id="__KG_DATA__" type="application/json">{json.dumps(data, ensure_ascii=False)}</script>
    <script src="/static/vendor/vis-network.min.js"></script>
    <script src="/static/vis-embed.js"></script>
    <script>
      window.addEventListener('DOMContentLoaded', () => {{
        window.__KG_INIT_VIS__('mynetwork', {json.dumps(search or "", ensure_ascii=False)});
      }});
    </script>
  </body>
</html>"""
        return HTMLResponse(html)
    except HTTPException:
        raise
    except Exception as ex:
        logger.exception("/visjs erro")
        return HTMLResponse(f"<pre>visjs error: {str(ex)}</pre>", status_code=500)

# -----------------------------------------------------------------------------
# PYVIS – HTML
# -----------------------------------------------------------------------------
@app.get("/pyvis")
async def vis_pyvis(
    faccao_id: Optional[int] = Query(None),
    include_co: bool = Query(True),
    max_pairs: int = Query(5000, ge=1, le=100000),
    search: Optional[str] = Query(None),
):
    try:
        from pyvis.network import Network

        raw = await supabase_rpc_get_graph(faccao_id, include_co, max_pairs)
        data = _sanitize_graph_for_vis(raw)

        net = Network(height="calc(100vh - 56px)", width="100%", directed=True, notebook=False)
        net.set_options(json.dumps({
            "nodes": {"shape": "dot", "size": 8, "font": {"size": 12}},
            "edges": {
                "smooth": False,
                "width": 0.5,           # MUITO finas
                "selectionWidth": 0.5,
                "arrows": {"to": {"enabled": True, "scaleFactor": 0.4}},
                "color": {"opacity": 0.35}
            },
            "interaction": {"hover": True, "dragNodes": True, "zoomView": True},
            "physics": {"enabled": True, "solver": "forceAtlas2Based", "stabilization": {"iterations": 400, "fit": True}}
        }))

        RED = "#D32F2F"; BLUE = "#0D47A1"; YELLOW = "#FFD700"; GREY = "#607D8B"

        label_map = {str(n["id"]): str(n.get("label", "")) for n in data["nodes"]}
        type_map  = {str(n["id"]): str(n.get("type", ""))  for n in data["nodes"]}

        # nós
        for n in data["nodes"]:
            nid = str(n["id"])
            label = n.get("label") or nid
            ntype = (n.get("type") or "").lower()
            color = GREY
            if ntype == "funcao": color = YELLOW
            elif "cv" in label.lower(): color = RED
            elif "pcc" in label.lower(): color = BLUE
            size = float(n.get("size", 8))
            net.add_node(nid, label=label, color=color, size=size)

        # arestas
        for e in data["edges"]:
            src, dst = str(e["from"]), str(e["to"])
            rel = e.get("relation") or ""
            edge_color = "#B0BEC5"
            if type_map.get(src) == "funcao" or type_map.get(dst) == "funcao" or rel in ("FUNCAO_DA_FACCAO", "EXERCE"):
                edge_color = YELLOW
            else:
                ls = label_map.get(src, "").lower()
                ld = label_map.get(dst, "").lower()
                if "cv" in ls or "cv" in ld: edge_color = RED
                if "pcc" in ls or "pcc" in ld: edge_color = BLUE
            net.add_edge(src, dst, arrows="to" if (e.get("arrows") == "to") else "", color=edge_color, width=0.5)

        html = net.generate_html(notebook=False)

        extra = f"""
<div class="kg-toolbar" style="position:fixed;left:0;right:0;top:0;background:#fff;border-bottom:1px solid #eee;z-index:10;padding:8px;display:flex;gap:8px;align-items:center;">
  <strong style="margin-right:8px;">Knowledge Graph (PyVis)</strong>
  <input id="kg-search" type="search" placeholder="Buscar nó…" style="flex:1;min-width:220px;padding:6px 10px;">
  <button id="btn-fit" type="button">Ajustar</button>
  <button id="btn-reload" type="button" onclick="location.reload()">Recarregar</button>
</div>
<script>
(function(){{
  function afterInit(){{
    if (typeof network === 'undefined') return setTimeout(afterInit, 50);

    network.once('stabilizationIterationsDone', function(){{ network.setOptions({{ physics:false }}); }});
    document.getElementById('btn-fit').onclick = function(){{ network.fit({{ animation:true }}); }};

    var input = document.getElementById('kg-search');
    function doSearch(term){{
      term = (term||'').trim().toLowerCase(); if(!term) return;
      var nodes = network.body.data.nodes.get();
      var hit = null;
      nodes.forEach(function(n){{
        network.body.data.nodes.update({{ id:n.id, borderWidth:0, font:{{size:12}} }});
        if(!hit){{
          var l = String(n.label||'').toLowerCase();
          if(l.includes(term) || String(n.id).toLowerCase().includes(term)) hit = n.id;
        }}
      }});
      if(hit){{
        network.body.data.nodes.update({{ id:hit, borderWidth:3, font:{{size:14}} }});
        network.focus(hit, {{ scale:1.2, animation:{{duration:500}} }});
        network.selectNodes([hit]);
      }}
    }}
    input.addEventListener('keydown', function(ev){{ if(ev.key==='Enter') doSearch(input.value); }});
    var initial = {json.dumps(search or "", ensure_ascii=False)};
    if(initial) setTimeout(function(){{ doSearch(initial); }}, 400);
  }}
  afterInit();
}})();
</script>
<style>body{{margin-top:56px;}}</style>
"""
        html = html.replace("</body>", f"{extra}\n</body>")
        return HTMLResponse(html)

    except HTTPException:
        raise
    except Exception as ex:
        logger.exception("/pyvis erro")
        return HTMLResponse(f"<pre>pyvis error: {str(ex)}</pre>", status_code=500)

# =============================================================================
# Fim de app.py
# =============================================================================
