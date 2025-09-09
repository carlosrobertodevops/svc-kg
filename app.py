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
