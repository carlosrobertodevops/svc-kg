import os
import json
import math
import asyncio
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import quote_plus

import httpx
from fastapi import FastAPI, Query, Response
from fastapi.responses import HTMLResponse, ORJSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

APP_NAME = "svc-kg"
APP_ENV = os.getenv("APP_ENV", "development")
PORT = int(os.getenv("PORT", "8080"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

# CORS
CORS_ALLOW_ORIGINS = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")]
CORS_ALLOW_METHODS = [m.strip() for m in os.getenv("CORS_ALLOW_METHODS", "GET,POST,OPTIONS").split(",")]
CORS_ALLOW_HEADERS = [h.strip() for h in os.getenv("CORS_ALLOW_HEADERS", "Authorization,Content-Type").split(",")]
CORS_ALLOW_CREDENTIALS = os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true"

# Supabase / PostgREST
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")  # opcional
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")  # compat
SUPABASE_RPC_FN = os.getenv("SUPABASE_RPC_FN", "get_graph_membros")
SUPABASE_TIMEOUT = int(os.getenv("SUPABASE_TIMEOUT", "15"))

# Colunas de fotos
MEMBERS_TABLE = os.getenv("MEMBERS_TABLE", "membros")
MEMBERS_ID_COL = os.getenv("MEMBERS_ID_COL", "id")
MEMBERS_PHOTO_COL = os.getenv("MEMBERS_PHOTO_COL") or os.getenv("MEMBERS_PHOTO", "photo_url")
FALLBACK_ICON = "/static/icons/person.svg"

app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS if CORS_ALLOW_ORIGINS != ["*"] else ["*"],
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=CORS_ALLOW_METHODS,
    allow_headers=CORS_ALLOW_HEADERS,
)

# ---------- util ----------

def _color_for_faccao(label: str) -> str:
    """CV == vermelho; qualquer facção cujo nome contenha 'PCC' == azul escuro; default amarelo."""
    name = (label or "").upper()
    if name == "CV":
        return "#C62828"  # red 800
    if "PCC" in name:
        return "#0D47A1"  # blue 900
    return "#C2A600"     # dourado/default nos exemplos

def _edge_color(relation: str) -> str:
    rel = (relation or "").upper()
    if rel == "PERTENCE_A":
        return "#888888"
    if rel == "EXERCE":
        return "#999999"
    return "#BBBBBB"

def _thin_width(weight: Optional[float]) -> int:
    # força as linhas a ficarem finas, mas deixa uma leve variação
    try:
        w = float(weight or 1.0)
    except Exception:
        w = 1.0
    return 1 if w <= 1 else 1

async def supabase_rpc_get_graph(
    faccao_id: int,
    include_co: bool,
    max_pairs: int,
) -> Dict[str, Any]:
    """
    Chama o RPC no Supabase/PostgREST.
    Importante: alguns PostgREST só aceitam os parâmetros nomeados como p_*.
    Faremos duas tentativas.
    """
    assert SUPABASE_URL, "SUPABASE_URL não configurada"

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    # Authorization (prioriza service key)
    token = SUPABASE_SERVICE_KEY or SUPABASE_ANON_KEY
    if token:
        headers["apikey"] = token
        headers["Authorization"] = f"Bearer {token}"

    url = f"{SUPABASE_URL}/rest/v1/rpc/{SUPABASE_RPC_FN}"

    payloads = [
        {"faccao_id": faccao_id, "include_co": include_co, "max_pairs": max_pairs},
        {"p_faccao_id": faccao_id, "p_include_co": include_co, "p_max_pairs": max_pairs},
    ]

    async with httpx.AsyncClient(timeout=SUPABASE_TIMEOUT) as client:
        last_exc = None
        for body in payloads:
            try:
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code == 200:
                    app.logger.info("RPC %s OK (var1 p_*).", SUPABASE_RPC_FN)
                    return resp.json()
                last_exc = RuntimeError(f"{resp.status_code}: {resp.text}")
            except Exception as e:
                last_exc = e
        raise RuntimeError(f"Supabase RPC {SUPABASE_RPC_FN} falhou: {last_exc}")

def attach_photos(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Adiciona 'photo_url' ao nó de membros se já vier no payload (photo_url/foto_path) ou usa ícone default.
    Mantém os demais nós inalterados.
    """
    for n in nodes:
        if n.get("type") == "membro":
            pic = n.get("photo_url") or n.get("foto_path")
            n["photo_url"] = pic if pic else FALLBACK_ICON
    return nodes

def to_visjs_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Converte o payload {nodes, edges} em objetos prontos para o vis-network."""
    nodes = attach_photos(list(raw.get("nodes", [])))
    edges = list(raw.get("edges", []))

    vis_nodes = []
    for n in nodes:
        node = {
            "id": n.get("id"),
            "label": n.get("label") or "",
        }
        ntype = n.get("type")
        if ntype == "faccao":
            node["color"] = {"background": _color_for_faccao(n.get("label")), "border": "#444"}
            node["shape"] = "dot"
            node["size"] = 22
        elif ntype == "membro":
            # foto circular quando houver
            if n.get("photo_url"):
                node["shape"] = "circularImage"
                node["image"] = n["photo_url"]
                node["size"] = 28
                node["borderWidth"] = 1
                node["color"] = {"border": "#555"}
            else:
                node["shape"] = "dot"
                node["size"] = 18
                node["color"] = {"background": "#607D8B", "border": "#37474F"}
        else:  # função etc.
            node["shape"] = "dot"
            node["size"] = 14
            node["color"] = {"background": "#BDBDBD", "border": "#616161"}
        vis_nodes.append(node)

    vis_edges = []
    for e in edges:
        vis_edges.append({
            "from": e.get("source"),
            "to": e.get("target"),
            "width": _thin_width(e.get("weight")),
            "color": {"color": _edge_color(e.get("relation"))},
            "arrows": "to" if e.get("relation") in ("PERTENCE_A", "EXERCE") else "",
            "smooth": False
        })
    return {"nodes": vis_nodes, "edges": vis_edges}

async def fetch_graph_sanitized(
    faccao_id: int,
    include_co: bool,
    max_pairs: int,
) -> Dict[str, Any]:
    raw = await supabase_rpc_get_graph(faccao_id, include_co, max_pairs)
    if not isinstance(raw, dict) or "nodes" not in raw:
        raise RuntimeError("payload inválido do RPC (esperado dict com 'nodes' e 'edges')")
    return raw

# ---------- health ----------

@app.get("/live", summary="Probe liveness", response_class=PlainTextResponse)
async def live() -> str:
    return "OK"

@app.get("/health", summary="Probe readiness", response_class=ORJSONResponse)
async def health() -> Dict[str, Any]:
    # checa PostgREST rapidamente (sem travar o serviço)
    probe = {"supabase": "unknown"}
    if SUPABASE_URL:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{SUPABASE_URL}/")
                probe["supabase"] = "ok" if r.status_code < 500 else f"bad:{r.status_code}"
        except Exception as e:
            probe["supabase"] = f"error:{type(e).__name__}"
    return {
        "service": APP_NAME,
        "env": APP_ENV,
        "status": "ok",
        "probes": probe,
    }

# ---------- API JSON (dados brutos) ----------

@app.get("/v1/graph/membros", response_class=ORJSONResponse, summary="Grafo de membros (JSON)")
async def graph_membros(
    faccao_id: int = Query(6, ge=0),
    include_co: bool = Query(True),
    max_pairs: int = Query(80, ge=1, le=500),
) -> Dict[str, Any]:
    data = await fetch_graph_sanitized(faccao_id, include_co, max_pairs)
    return data

# ---------- VIS.JS (HTML) ----------

HTML_TEMPLATE_VIS = """<!doctype html>
<html lang="pt-br">
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>{title}</title>
    <link rel="stylesheet" href="/static/vendor/vis-network.min.css">
    <link rel="stylesheet" href="/static/vis-style.css">
  </head>
  <body data-theme="{theme}">
    <div class="kg-toolbar">
      <h4 class="kg-title">{title}</h4>
      <input id="kg-search" type="search" placeholder="Buscar nó (membro/facção/função)…"/>
      <button id="btn-apply">Buscar</button>
      <button id="btn-clear">Limpar</button>
      <button id="btn-print">Print</button>
      <button id="btn-reload">Reload</button>
    </div>
    <div id="mynetwork" style="height:90vh;width:100%;"></div>

    <script>window.__KG_DATA__ = {data_json};</script>
    <script src="/static/vendor/vis-network.min.js"></script>
    <script>
      (function(){
        const raw = window.__KG_DATA__ || {{nodes:[],edges:[]}};
        const nodes = new vis.DataSet(raw.nodes);
        const edges = new vis.DataSet(raw.edges);

        const container = document.getElementById('mynetwork');
        const data = {{ nodes, edges }};
        const options = {{
          autoResize: true,
          interaction: {{
            hover: true,
            dragNodes: true,
            dragView: true,
            zoomView: true,
            multiselect: false
          }},
          physics: {{
            enabled: true,
            stabilization: false,   // não re-renderiza tudo ao puxar um nó
            solver: "barnesHut",
            barnesHut: {{ gravitationalConstant: -8000, springLength: 140, springConstant: 0.02 }}
          }},
          nodes: {{
            font: {{ face: "Inter, system-ui, Arial", size: 12 }},
            borderWidth: 1
          }},
          edges: {{
            width: 1,
            smooth: false
          }}
        }};
        const network = new vis.Network(container, data, options);

        // busca por label (membro/facção/função)
        function searchAndFocus(q){
          if(!q) return;
          const term = q.trim().toLowerCase();
          const found = nodes.get({{ filter: n => (n.label||"").toLowerCase().includes(term) }});
          if(found && found.length) {{
            const ids = found.map(n => n.id);
            network.selectNodes(ids);
            network.fit({{ nodes: ids, animation: {{ duration: 500 }}}});
          }}
        }

        document.getElementById('btn-apply').onclick = () => {{
          const q = document.getElementById('kg-search').value;
          searchAndFocus(q);
        }};
        document.getElementById('btn-clear').onclick = () => {{
          document.getElementById('kg-search').value = "";
          network.unselectAll();
        }};
        document.getElementById('btn-print').onclick = () => window.print();
        document.getElementById('btn-reload').onclick = () => window.location.reload();

        // Enter para buscar
        document.getElementById('kg-search').addEventListener('keydown', (e) => {{
          if(e.key === 'Enter') document.getElementById('btn-apply').click();
        }});
      })();
    </script>
  </body>
</html>
"""

@app.get(
    "/v1/vis/visjs",
    summary="Visualização vis-network (HTML)",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {}}, "description": "HTML da visualização (vis.js)"}}
)
async def vis_visjs(
    faccao_id: int = Query(6, ge=0),
    include_co: bool = Query(True),
    max_pairs: int = Query(80, ge=1, le=500),
    theme: str = Query("light"),
    title: str = Query("Knowledge Graph (vis.js)")
) -> HTMLResponse:
    raw = await fetch_graph_sanitized(faccao_id, include_co, max_pairs)
    vis_payload = to_visjs_payload(raw)
    html = HTML_TEMPLATE_VIS.format(
        title=title,
        theme=theme,
        data_json=json.dumps(vis_payload, ensure_ascii=False)
    )
    return HTMLResponse(content=html, status_code=200, media_type="text/html")

# ---------- PYVIS (HTML) ----------

@app.get(
    "/v1/vis/pyvis",
    summary="Visualização PyVis (HTML)",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {}}, "description": "HTML gerado pelo PyVis"}}
)
async def vis_pyvis(
    faccao_id: int = Query(6, ge=0),
    include_co: bool = Query(True),
    max_pairs: int = Query(80, ge=1, le=500),
    theme: str = Query("light"),
    title: str = Query("Knowledge Graph (PyVis)"),
) -> HTMLResponse:
    from pyvis.network import Network

    raw = await fetch_graph_sanitized(faccao_id, include_co, max_pairs)
    nodes = attach_photos(list(raw.get("nodes", [])))
    edges = list(raw.get("edges", []))

    net = Network(height="90vh", width="100%", bgcolor="#ffffff" if theme=="light" else "#121212", directed=True)
    net.barnes_hut(gravity=-8000, spring_length=140, spring_strength=0.02)

    # Nós
    for n in nodes:
        nid = n.get("id")
        label = n.get("label") or ""
        ntype = n.get("type")
        if ntype == "faccao":
            color = _color_for_faccao(label)
            net.add_node(nid, label=label, color=color, shape="dot", size=22)
        elif ntype == "membro":
            pic = n.get("photo_url")
            if pic:
                net.add_node(nid, label=label, shape="circularImage", image=pic, size=28, borderWidth=1)
            else:
                net.add_node(nid, label=label, shape="dot", color="#607D8B", size=18)
        else:
            net.add_node(nid, label=label, shape="dot", color="#BDBDBD", size=14)

    # Arestas finas
    for e in edges:
        net.add_edge(
            e.get("source"),
            e.get("target"),
            width=_thin_width(e.get("weight")),
            color=_edge_color(e.get("relation")),
            arrows="to" if e.get("relation") in ("PERTENCE_A","EXERCE") else ""
        )

    # Opções (JSON válido)
    net.set_options(json.dumps({
        "physics": {"enabled": True, "stabilization": False, "solver": "barnesHut"},
        "interaction": {"hover": True, "dragNodes": True, "dragView": True, "zoomView": True},
        "edges": {"smooth": False, "width": 1},
        "nodes": {"font": {"size": 12}}
    }))

    # pyvis 0.3.2 não aceita title em generate_html
    html = net.generate_html()
    # injeta título simples
    html = html.replace("<title>PyVis Network</title>", f"<title>{title}</title>")
    return HTMLResponse(content=html, status_code=200, media_type="text/html")

# ---------- Docs simples ----------

DOCS_HTML = """<!doctype html>
<html lang="pt-br"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>sic-kg /docs</title>
<link rel="stylesheet" href="/static/vis-style.css"/>
</head><body class="doc-body">
<h1>sic-kg — documentação</h1>

<section>
<h2>Health</h2>
<ul>
  <li><code>GET /live</code> — liveness probe (texto <em>OK</em>).</li>
  <li><code>GET /health</code> — readiness JSON (status do serviço e <em>probe</em> rápido do Supabase/PostgREST).</li>
</ul>
</section>

<section>
<h2>Dados (JSON)</h2>
<ul>
  <li><code>GET /v1/graph/membros?faccao_id=6&include_co=true&max_pairs=80</code> — payload bruto <code>{nodes, edges}</code> vindo do RPC <code>{rpc}</code>.</li>
</ul>
</section>

<section>
<h2>Visualizações (HTML)</h2>
<p>Estes endpoints devolvem <strong>HTML</strong>, não JSON. Por isso, no Swagger podem aparecer como “Undocumented / 200” quando chamados via UI. Acesse-os direto no navegador:</p>
<ul>
  <li><code>/v1/vis/visjs?faccao_id=6&include_co=true&max_pairs=80&theme=light&title=Knowledge%20Graph%20(vis.js)</code></li>
  <li><code>/v1/vis/pyvis?faccao_id=6&include_co=true&max_pairs=80&theme=light&title=Knowledge%20Graph%20(PyVis)</code></li>
</ul>
<p>Funcionalidades: busca por membro/facção/função; arestas finas; cores: CV em vermelho, facções com “PCC” em azul escuro; ao arrastar um nó, somente ele se move (sem re-estabilização completa); foto circular para membros quando houver <code>photo_url</code> (ou <code>foto_path</code>) no nó; ícone padrão quando ausente.</p>
</section>

<section>
<h2>Configuração</h2>
<p>Variáveis relevantes (já usadas no serviço em execução):</p>
<pre>SUPABASE_URL={supabase}
SUPABASE_RPC_FN={rpc}
SUPABASE_* (chaves)
</pre>
</section>

<footer><small>© sic-kg</small></footer>
</body></html>
"""

@app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
async def docs_page():
    return HTMLResponse(DOCS_HTML.format(
        supabase=SUPABASE_URL or "(não definido)",
        rpc=SUPABASE_RPC_FN
    ))
