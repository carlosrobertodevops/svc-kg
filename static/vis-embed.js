// =============================================================================
// Arquivo: static/vis-embed.js
// Versão: v1.7.20
// Objetivo: Inicializar vis-network lendo JSON embutido; dedup; cores CV/PCC/funções;
//           arestas muito finas; estabiliza e desliga física; busca com destaque.
// Funções/métodos:
// - __KG_INIT_VIS__(containerId, initialSearch): bootstrap
// - mapColors(node, edge): regras de cor
// - buildDataFromEmbedded(): lê <script id="__KG_DATA__"> e normaliza
// - attachSearch(network, nodes): busca + foco
// ==============================================================

(function(){
  const container = document.getElementById('mynetwork');
  if (!container) return;

  // Cores
  const COLOR_CV  = '#d32f2f';  // vermelho
  const COLOR_PCC = '#0d47a1';  // azul escuro
  const COLOR_FUN = '#fdd835';  // amarelo
  const COLOR_DEF = '#607d8b';

  const EDGE_COLORS = {
    'PERTENCE_A':      '#9e9e9e',
    'EXERCE':          COLOR_FUN,
    'FUNCAO_DA_FACCAO':COLOR_FUN,
    'CO_FACCAO':       '#8e24aa',
    'CO_FUNCAO':       '#546e7a'
  };

  const source = container.getAttribute('data-source') || 'server';
  const endpoint = container.getAttribute('data-endpoint') || '/v1/graph/membros';

  function isPgTextArray(s) { s=(s||'').trim(); return s.length>=2 && s[0]=='{' && s[s.length-1]=='}'; }
  function cleanLabel(raw) {
    if(!raw) return '';
    const s=String(raw).trim();
    if(!isPgTextArray(s)) return s;
    const inner=s.slice(1,-1); if(!inner) return '';
    return inner.replace(/(^|,)\\s*"?null"?\\s*(?=,|$)/gi,'').replace(/"/g,'').split(',').map(x=>x.trim()).filter(Boolean).join(', ');
  }

  function hasTagPCC(name){
    const s = (name||'').toUpperCase();
    return /\\bPCC\\b/.test(s) || s.includes('PCC');
  }

  function hasTagCV(name){
    const s = (name||'').toUpperCase();
    return /\\bCV\\b/.test(s) || s.includes('CV');
  }

  function colorObj(hex, opacity){
    const c = hex || COLOR_DEF;
    return {
      background: c,
      border: c,
      highlight: { background: c, border: c },
      hover: { background: c, border: c },
      opacity: (typeof opacity==='number' ? opacity : 1)
    };
  }

  function inferFaccaoColors(rawNodes) {
    const map={};
    rawNodes.forEach(n=>{
      if(!n) return;
      const t=String(n.type||'').toLowerCase();
      if (t!=='faccao') return;
      const label = cleanLabel(n.label||'');
      const id = String(n.id);
      if (hasTagPCC(label)) map[id] = COLOR_PCC;
      else if (hasTagCV(label)) map[id] = COLOR_CV;
    });
    return map;
  }

  function decideNodeHex(n, faccaoColorById, labelClean){
    const t = String(n.type||'').toLowerCase();
    const name = labelClean || '';
    if (t === 'faccao'){
      if (hasTagPCC(name)) return COLOR_PCC;
      if (hasTagCV(name))  return COLOR_CV;
    }
    const gid = String(n.group ?? n.faccao_id ?? '');
    if (gid && faccaoColorById[gid]) return faccaoColorById[gid];
    if (t === 'funcao') return COLOR_FUN;
    if (hasTagPCC(name)) return COLOR_PCC;
    if (hasTagCV(name))  return COLOR_CV;
    return COLOR_DEF;
  }

  function edgeStyleFor(rel){ return { color: EDGE_COLORS[rel] || '#b0bec5', width: 0.1 }; }
  function degreeMap(nodes,edges) {
    const d={}; nodes.forEach(n=>d[n.id]=0);
    edges.forEach(e=>{ if(e.from in d) d[e.from]++; if(e.to in d) d[e.to]++; });
    return d;
  }

  function render(data){
    const rawNodes = data.nodes || [];
    const rawEdges = data.edges || [];

    const faccaoColorById = inferFaccaoColors(rawNodes);

    const nodes = [];
    const seen = new Set();
    for (const n0 of rawNodes) {
      if (!n0 || n0.id==null) continue;
      const id = String(n0.id);
      if (seen.has(id)) continue; seen.add(id);

      const label = cleanLabel(n0.label) || id;
      const isFaction = (String(n0.type||'').toLowerCase()==='faccao');

      const groupId = isFaction ? id
        : (n0.faccao_id!=null ? String(n0.faccao_id)
           : (n0.group!=null ? String(n0.group) : (n0.type || '0')));

      const hex = decideNodeHex({group: groupId, faccao_id: n0.faccao_id, type: n0.type}, faccaoColorById, label);
      const photo = n0.photo_url && /^https?:\\/\\//i.test(n0.photo_url) ? n0.photo_url : null;

      const base = { id, label, group: groupId, color: colorObj(hex, 1), borderWidth: 1 };
      if (photo) { base.shape='circularImage'; base.image=photo; } else { base.shape='dot'; }
      nodes.push(base);
    }

    const nodeIds = new Set(nodes.map(n=>n.id));
    const edges = [];
    for (const e of (rawEdges||[])) {
      if(!e) continue;
      const a = String(e.source), b = String(e.target);
      if(!nodeIds.has(a) || !nodeIds.has(b)) continue;
      const rel = e.relation || '';
      const style = edgeStyleFor(rel);
      edges.push({ from:a, to:b, value: Number(e.weight||1), width: style.width, color: style.color, title: rel });
    }

    if (!nodes.length) {
      container.innerHTML = '<div style="padding:12px">Sem dados.</div>';
      return;
    }

    const deg = degreeMap(nodes, edges);
    nodes.forEach(n=>{ const d=deg[n.id]||0; n.value = 12 + Math.log(d+1)*10; });

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

    const q = document.getElementById('kg-search');
    if (q) {
      function dim(n){ const bg = (n.color&&n.color.background)? n.color.background : n.color; return colorObj(bg, 0.25); }
      function full(n){ const bg = (n.color&&n.color.background)? n.color.background : n.color; return colorObj(bg, 1); }
      function run(){
        const t=(q.value||'').trim().toLowerCase(); if(!t) return;
        const all=dsNodes.get();
        const hits=all.filter(n => (n.label||'').toLowerCase().includes(t) || String(n.id)===t);
        if(!hits.length) return;
        all.forEach(n => dsNodes.update({ id: n.id, color: dim(n) }));
        hits.forEach(h => dsNodes.update({ id: h.id, color: full(dsNodes.get(h.id)) }));
        net.setOptions({ physics: false });
        net.fit({ nodes: hits.map(h=>h.id), animation: { duration: 300 } });
      }
      q.addEventListener('keydown', e=>{ if(e.key==='Enter') run(); });
    }

    const p=document.getElementById('btn-print'); if(p) p.onclick=()=>window.print();
    const r=document.getElementById('btn-reload'); if(r) r.onclick=()=>location.reload();
  }

  function run(){
    if (source === 'server'){
      const tag=document.getElementById('__KG_DATA__');
      if(!tag){ container.innerHTML='<div style="padding:12px">Dados não incorporados.</div>'; return; }
      try { render(JSON.parse(tag.textContent||'{}')); }
      catch(e){ container.innerHTML='<pre>'+String(e)+'</pre>'; }
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

  if (document.readyState!=='loading') run(); else document.addEventListener('DOMContentLoaded', run);
})();


// (function () {
//   const RED = "#D32F2F";     // CV
//   const BLUE = "#0D47A1";    // PCC
//   const YELLOW = "#FFD700";  // funções
//   const GREY = "#607D8B";
//   const EDGE_DEFAULT = "#B0BEC5";

//   function mapColors(node, edge, labelMap, typeMap) {
//     if (node) {
//       const label = String(node.label || "").toLowerCase();
//       const type = String(node.type || "").toLowerCase();
//       if (type === "funcao") return YELLOW;
//       if (label.includes("cv")) return RED;
//       if (label.includes("pcc")) return BLUE;
//       return GREY;
//     }
//     if (edge) {
//       const src = String(edge.from);
//       const dst = String(edge.to);
//       const rel = String(edge.relation || "");
//       if (typeMap[src] === "funcao" || typeMap[dst] === "funcao" || rel === "FUNCAO_DA_FACCAO" || rel === "EXERCE") {
//         return YELLOW;
//       }
//       const ls = String(labelMap[src] || "").toLowerCase();
//       const ld = String(labelMap[dst] || "").toLowerCase();
//       if (ls.includes("cv") || ld.includes("cv")) return RED;
//       if (ls.includes("pcc") || ld.includes("pcc")) return BLUE;
//       return EDGE_DEFAULT;
//     }
//     return GREY;
//   }

//   function buildDataFromEmbedded() {
//     const el = document.getElementById("__KG_DATA__");
//     if (!el) return { nodes: [], edges: [] };
//     let data;
//     try { data = JSON.parse(el.textContent || "{}"); }
//     catch { data = { nodes: [], edges: [] }; }

//     // Dedup de nós e normalização
//     const nodes = [];
//     const seenNodes = new Set();
//     (data.nodes || []).forEach(n => {
//       const id = String(n.id);
//       if (!id || seenNodes.has(id)) return;
//       seenNodes.add(id);
//       nodes.push({
//         id,
//         label: String(n.label || id),
//         type: String(n.type || "").toLowerCase(),
//         size: Number(n.size || 8),
//         group: n.group
//       });
//     });

//     // Dedup de arestas (source/target -> from/to)
//     const edges = [];
//     const seenEdges = new Set();
//     (data.edges || []).forEach(e => {
//       const from = String(e.from || e.source || "");
//       const to = String(e.to || e.target || "");
//       if (!from || !to) return;
//       const rel = String(e.relation || "");
//       const key = `${from}::${to}::${rel}`;
//       if (seenEdges.has(key)) return;
//       seenEdges.add(key);
//       edges.push({
//         from, to,
//         relation: rel,
//         value: Number(e.value || e.weight || 1),
//         arrows: e.arrows === "to" ? "to" : ""
//       });
//     });

//     return { nodes, edges };
//   }

//   function attachSearch(network, nodes) {
//     const input = document.getElementById("kg-search");
//     if (!input) return;
//     function doSearch(term) {
//       term = (term || "").trim().toLowerCase();
//       if (!term) return;
//       const all = nodes.get();
//       let hitId = null;
//       all.forEach(n => {
//         nodes.update({ id: n.id, borderWidth: 0, font: { size: 12 } });
//         if (!hitId) {
//           const l = String(n.label || "").toLowerCase();
//           if (l.includes(term) || String(n.id).toLowerCase().includes(term)) hitId = n.id;
//         }
//       });
//       if (hitId) {
//         nodes.update({ id: hitId, borderWidth: 3, font: { size: 14 } });
//         network.focus(hitId, { scale: 1.2, animation: { duration: 500 } });
//         network.selectNodes([hitId]);
//       }
//     }
//     input.addEventListener("keydown", ev => { if (ev.key === "Enter") doSearch(input.value); });
//   }

//   window.__KG_INIT_VIS__ = function (containerId, initialSearch) {
//     const container = document.getElementById(containerId);
//     if (!container) return;

//     // Usa somente o JSON incorporado (data-source="server")
//     const dataset = buildDataFromEmbedded();

//     // índices auxiliares
//     const labelMap = {};
//     const typeMap = {};
//     dataset.nodes.forEach(n => { labelMap[n.id] = n.label || ""; typeMap[n.id] = n.type || ""; });

//     // monta DataSet com cores aplicadas
//     const nodes = new vis.DataSet(dataset.nodes.map(n => ({
//       id: n.id,
//       label: n.label,
//       size: n.size,
//       color: mapColors(n, null, labelMap, typeMap)
//     })));

//     const edges = new vis.DataSet(dataset.edges.map(e => ({
//       from: e.from,
//       to: e.to,
//       arrows: e.arrows ? { to: { enabled: true, scaleFactor: 0.4 } } : "",
//       color: { color: mapColors(null, e, labelMap, typeMap), opacity: 0.35 },
//       width: 0.5,     // arestas MUITO finas
//       smooth: false
//     })));

//     const data = { nodes, edges };
//     const options = {
//       nodes: { shape: "dot", font: { size: 12 } },
//       edges: { width: 0.5, selectionWidth: 0.5, smooth: false, color: { opacity: 0.35 } },
//       interaction: { hover: true, dragNodes: true, zoomView: true },
//       physics: { enabled: true, solver: "forceAtlas2Based", stabilization: { iterations: 400, fit: true } }
//     };

//     const network = new vis.Network(container, data, options);

//     // Desliga física após estabilização (para parar o movimento)
//     network.once("stabilizationIterationsDone", function () {
//       network.setOptions({ physics: false });
//     });

//     // Botão Ajustar (se existir)
//     const btnFit = document.getElementById("btn-fit");
//     if (btnFit) btnFit.onclick = () => network.fit({ animation: true });

//     // Busca
//     attachSearch(network, nodes);

//     // Busca inicial (se veio via querystring)
//     if (initialSearch && String(initialSearch).trim()) {
//       setTimeout(() => {
//         const input = document.getElementById("kg-search");
//         if (input) {
//           input.value = initialSearch;
//           const ev = new KeyboardEvent("keydown", { key: "Enter" });
//           input.dispatchEvent(ev);
//         }
//       }, 400);
//     }
//   };
// })();
