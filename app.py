import os, json, httpx, colorsys, time
from typing import Optional
from fastapi import FastAPI, Response, Request, Body, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pyvis.network import Network

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
RPC_NAME = os.getenv("RPC_NAME", "get_graph_membros")
EXPORT_TOKEN = os.getenv("EXPORT_TOKEN")
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS","").split(",") if o.strip()]

app = FastAPI(title="KG PyVis Microservice", version="1.0.0")

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

def _check_env():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Defina SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY")

def _group_color(g: int) -> str:
    hue = (abs(g) * 137) % 360
    r, l, s = colorsys.hls_to_rgb(hue/360.0, 0.55, 0.85)
    return '#%02x%02x%02x' % (int(r*255), int(l*255), int(s*255))

def fetch_graph_json(p_faccao_id: Optional[int], p_include_co: bool, p_max_pairs: int):
    _check_env()
    url = f"{SUPABASE_URL}/rest/v1/rpc/{RPC_NAME}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "p_faccao_id": p_faccao_id,
        "p_include_co": p_include_co,
        "p_max_pairs": p_max_pairs,
    }
    with httpx.Client(timeout=30) as client:
        r = client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    if isinstance(data, str):
        data = json.loads(data)
    return data

def build_pyvis_html(
    data: dict,
    physics=True, hierarchical=False,
    size_by_degree=True, weighted_degree=True,
    min_size=10, max_size=46, arrows=True,
    bg="#0B1020", font_color="#e6eefb",
):
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []

    # degree
    deg = {}
    if size_by_degree:
        for e in edges:
            s, t = str(e.get("source","")), str(e.get("target",""))
            w = float(e.get("weight", 1.0) if weighted_degree else 1.0)
            deg[s] = deg.get(s, 0.0) + w
            deg[t] = deg.get(t, 0.0) + w

    net = Network(height="100%", width="100%", bgcolor=bg, font_color=font_color, directed=True)
    options = {
      "physics": {
        "enabled": physics,
        "barnesHut": {"gravitationalConstant": -25000, "centralGravity": 0.12, "springLength": 120, "springConstant": 0.02, "damping": 0.86, "avoidOverlap": 0.2},
        "minVelocity": 0.1, "solver": "barnesHut", "timestep": 0.5
      },
      "interaction": {"hover": True, "navigationButtons": False, "multiselect": True, "zoomView": True, "dragNodes": True, "dragView": True},
      "edges": {"smooth": {"type": "dynamic"}, "color": {"color": "#9AA4B2", "highlight": "#7df9ff"}, "arrows": {"to": {"enabled": arrows}}},
      "nodes": {"borderWidth": 1, "shape": "dot"},
      "layout": { "improvedLayout": True, "hierarchical": {"enabled": hierarchical, "direction": "UD", "sortMethod": "hubsize"}}
    }
    net.set_options(json.dumps(options))

    # nodes
    maxdeg = max(deg.values()) if deg else 1.0
    for n in nodes:
        nid = str(n.get("id",""))
        if not nid: 
            continue
        label = str(n.get("label", nid))
        ntype = str(n.get("type","membro"))
        group = int(n.get("group", 0))
        base = float(n.get("size", 14.0))
        d = deg.get(nid, 0.0)
        size = max(min_size, min(max_size,
                 (base if not size_by_degree else min_size + (max_size-min_size) * ((d**0.5)/(maxdeg**0.5)))) )
        color = _group_color(group)
        title = f"<b>{label}</b><br>Tipo: {ntype}<br>Grupo: {group}<br>Grau: {d:.2f}"
        net.add_node(nid, label=label, title=title, color=color, size=size, physics=physics, shape='dot')

    # edges
    for e in edges:
        s, t = str(e.get("source","")), str(e.get("target",""))
        if not s or not t: continue
        rel = str(e.get("relation",""))
        w = float(e.get("weight", 1.0))
        title = f"Relação: {rel or '-'}<br>Peso: {w:.2f}"
        net.add_edge(s, t, title=title, value=w, arrows='to' if arrows else None)

    html = net.generate_html()
    return _inject_controls(html)

def _inject_controls(html: str) -> str:
    # painel de busca/fit/export/print + bridge para FF (doubleClick)
    CTRL = r"""
<style>
#ff-ctl{position:fixed;top:10px;left:10px;z-index:9999;background:#0e1525ee;color:#cfe7ff;border:1px solid #27e1ff55;border-radius:10px;padding:10px;font-family:system-ui}
#ff-ctl input{background:#122338;color:#e6eefb;border:1px solid #27e1ff33;border-radius:8px;padding:6px 8px;outline:none;min-width:220px}
#ff-ctl button{margin-left:6px;background:#1b2a44;color:#cfe7ff;border:1px solid #27e1ff44;border-radius:8px;padding:6px 8px;cursor:pointer}
#ff-ctl .chip{margin-left:8px;font-size:12px;display:inline-flex;align-items:center}
#ff-ctl .chip input{margin-right:4px}
#ff-rail{position:fixed;right:10px;top:50%;transform:translateY(-50%);z-index:9999;background:#0e1525ee;border:1px solid #27e1ff55;border-radius:10px;padding:6px}
#ff-rail button{display:block;margin:6px;background:#1b2a44;color:#cfe7ff;border:1px solid #27e1ff44;border-radius:8px;padding:6px 8px;cursor:pointer}
</style>
<div id="ff-ctl">
  <input id="ff-search" placeholder='Buscar: "Maria", "fac:Comando", "12"...'>
  <button onclick="ffRunSearch()">Ir</button><button onclick="ffClear()">Limpar</button>
  <span class="chip"><input id="ff-filter" type="checkbox">Filtrar</span>
  <span class="chip"><input id="ff-hier" type="checkbox">Hierárquico</span>
</div>
<div id="ff-rail">
  <button onclick="ffFit()">Fit</button>
  <button onclick="ffExport()">PNG</button>
  <button onclick="window.print()">PDF</button>
</div>
<script>
function ready(cb){ if (window.network) cb(); else setTimeout(()=>ready(cb), 80); }
let matches=[], cur=-1;
function ffRunSearch(){
  const q=document.getElementById('ff-search').value.trim().toLowerCase();
  ready(()=>{
    matches=[]; cur=-1;
    const fac=q.startsWith('fac:'); const core=fac?q.substring(4).trim():q;
    const all=nodes.get();
    for(const n of all){
      const label=(n.label||'').toLowerCase();
      const group=(''+(n.group??'')).toLowerCase();
      const id=(''+(n.id??'')).toLowerCase();
      let ok=false;
      if(fac){ ok = n.type==='faccao' && (label.includes(core)||group===core||id===core); }
      else if(/^\d+$/.test(core)){ ok = (group===core||id===core||label.includes(core)); }
      else { ok = label.includes(core); }
      if(ok) matches.push(n.id);
    }
    if(matches.length){ cur=0; focus(matches[cur]); }
    if(document.getElementById('ff-filter').checked){ applyFilter(); } else { clearFilter(); }
  });
}
function ffClear(){
  document.getElementById('ff-search').value='';
  ready(()=>{ matches=[]; cur=-1; clearFilter(); network.unselectAll(); nodes.update(nodes.get().map(n=>({id:n.id, font:{strokeWidth:0}}))); });
}
function applyFilter(){ const s=new Set(matches); nodes.update(nodes.get().map(n=>({id:n.id, hidden: !s.has(n.id)}))); }
function clearFilter(){ nodes.update(nodes.get().map(n=>({id:n.id, hidden:false}))); }
function focus(id){
  ready(()=>{
    network.selectNodes([id]);
    network.focus(id,{scale:1.12, animation:{duration:500}});
    const nbrs = network.getConnectedNodes(id);
    nodes.update(nodes.get().map(n=>({id:n.id, opacity: (n.id===id||nbrs.includes(n.id))?1:0.25, font:{strokeWidth:(n.id===id)?2:0}})));
  });
}
function ffFit(){ ready(()=>network.fit({nodes: matches.length? matches:undefined, animation:true})); }
function ffExport(){ const url = network?.canvas?.frame?.canvas?.toDataURL('image/png'); if(url){ const a=document.createElement('a'); a.href=url; a.download='graph.png'; a.click(); } }
document.getElementById('ff-hier').addEventListener('change', (e)=>{ network.setOptions({ layout:{ hierarchical:{ enabled:e.target.checked, direction:'UD', sortMethod:'hubsize' } } }); network.stabilize(); });
// duplo clique → envia para o app (postMessage + hash)
ready(()=>{
  network.on('doubleClick', function(p){
    if(p?.nodes?.length){ const id=p.nodes[0];
      try{ window.parent.postMessage({type:'ff-node', id:id}, '*'); }catch(e){}
      const url = new URL(window.location.href); url.hash = 'ff-node?id='+encodeURIComponent(id); window.location.assign(url.toString());
    }
  });
});
</script>
"""
    return html.replace("<body>", "<body>"+CTRL)

@app.get("/health", response_class=PlainTextResponse)
def health(): return "ok"

@app.get("/v1/graph/data", response_class=JSONResponse)
def graph_data(p_faccao_id: Optional[int] = None, p_include_co: bool = True, p_max_pairs: int = 20000):
    try:
        return fetch_graph_json(p_faccao_id, p_include_co, p_max_pairs)
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/v1/graph/html", response_class=HTMLResponse)
def graph_html(
    p_faccao_id: Optional[int] = None,
    p_include_co: bool = True,
    p_max_pairs: int = 20000,
    physics: bool = True,
    hierarchical: bool = False,
    size_by_degree: bool = True,
    weighted_degree: bool = True,
    min_size: int = 10,
    max_size: int = 46,
    arrows: bool = True,
):
    try:
        data = fetch_graph_json(p_faccao_id, p_include_co, p_max_pairs)
        html = build_pyvis_html(
            data, physics=physics, hierarchical=hierarchical,
            size_by_degree=size_by_degree, weighted_degree=weighted_degree,
            min_size=min_size, max_size=max_size, arrows=arrows
        )
        return HTMLResponse(content=html, headers={"Cache-Control":"no-store","Access-Control-Allow-Origin":"*"})
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/v1/graph/export")
def export_graph(
    path: str = Body(..., embed=True),
    p_faccao_id: Optional[int] = Body(None),
    x_api_key: Optional[str] = Header(None, convert_underscores=False),
):
    if not EXPORT_TOKEN or x_api_key != EXPORT_TOKEN:
        raise HTTPException(401, "unauthorized")
    # build HTML
    data = fetch_graph_json(p_faccao_id, True, 20000)
    html = build_pyvis_html(data)
    # upload to Supabase Storage (bucket 'kg')
    url = f"{SUPABASE_URL}/storage/v1/object/kg/{path}"
    headers = {"Authorization": f"Bearer {SUPABASE_KEY}",
               "apikey": SUPABASE_KEY,
               "Content-Type": "text/html"}
    r = httpx.post(url, headers=headers, content=html.encode("utf-8"), timeout=30)
    r.raise_for_status()
    return {"ok": True, "path": path, "ts": int(time.time())}
