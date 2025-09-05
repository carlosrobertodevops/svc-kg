// =============================================================================
// Arquivo: static/vis-embed.js
// Versão: v1.7.20 (arestas ultrafinas + busca com destaque + física off pós-layout)
// Objetivo: Renderizar o grafo do /visjs com vis-network.
// Funções/métodos:
// - run(): decide fonte (server/client) e chama render()
// - render(data): normaliza, DEDUPlica nós por id, mapeia edges e monta vis.Network
// - attachToolbar(): busca com destaque (esmaece demais), print/reload
// - inferFaccaoColors()/colorForNode(): coloração CV/PCC/função
// - edgeStyleFor(): cor base por relação
// - cleanLabel()/hashColor()/degreeMap(): utilitários
// =============================================================================
(function () {
  const COLOR_CV = '#d32f2f';
  const COLOR_PCC = '#0d47a1';
  const COLOR_FUNCAO = '#c8a600';

  const EDGE_COLORS = {
    'PERTENCE_A': '#9e9e9e',
    'EXERCE': '#00796b',
    'FUNCAO_DA_FACCAO': '#ef6c00',
    'CO_FACCAO': '#8e24aa',
    'CO_FUNCAO': '#546e7a'
  };

  function hashColor(s) {
    s = String(s || '');
    let h = 0;
    for (let i = 0; i < s.length; i++) { h = (h << 5) - h + s.charCodeAt(i); h |= 0; }
    const hue = Math.abs(h) % 360; return `hsl(${hue},70%,50%)`;
  }
  function isPgTextArray(s) { s = (s || '').trim(); return s.length >= 2 && s[0] === '{' && s[s.length - 1] === '}'; }
  function cleanLabel(raw) {
    if (!raw) return '';
    const s = String(raw).trim();
    if (!isPgTextArray(s)) return s;
    const inner = s.slice(1, -1);
    if (!inner) return '';
    return inner
      .split(',')
      .map(x => x.trim().replace(/^"(.*)"$/, '$1').replace(/\\"/g, '"'))
      .filter(Boolean)
      .join(', ');
  }
  function degreeMap(nodes, edges) {
    const d = {}; nodes.forEach(n => d[n.id] = 0);
    edges.forEach(e => { d[e.from]++; d[e.to]++; });
    return d;
  }
  function inferFaccaoColors(rawNodes) {
    const map = new Map();
    rawNodes.forEach(n => {
      const group = String(n.group ?? n.faccao_id ?? '');
      const name = (n.label || '').toString().toUpperCase();
      if (!group) return;
      if (name.includes('PCC')) map.set(group, 'PCC');
      else if (name === 'CV' || name.includes('CV')) map.set(group, 'CV');
    });
    return map;
  }
  function colorForNode(n, faccaoColorById) {
    if ((n.type || '').toString().toLowerCase() === 'funcao' || String(n.group) === '6') return COLOR_FUNCAO;
    const gid = String(n.group ?? n.faccao_id ?? '');
    const tag = gid && faccaoColorById.get(gid);
    if (tag === 'PCC') return COLOR_PCC;
    if (tag === 'CV') return COLOR_CV;
    const lbl = (n.label || '').toString().toUpperCase();
    if (lbl.includes('PCC')) return COLOR_PCC;
    if (lbl === 'CV' || lbl.includes('CV')) return COLOR_CV;
    return hashColor(gid || lbl);
  }
  function edgeStyleFor(rel) {
    const base = EDGE_COLORS[rel] || '#9e9e9e';
    return { color: { color: base, highlight: base, hover: base }, opacity: 0.65 };
  }

  function attachToolbar(net, dsNodes) {
    const q = document.getElementById('kg-search');
    const btnPrint = document.getElementById('btn-print');
    const btnReload = document.getElementById('btn-reload');
    if (btnPrint) btnPrint.onclick = () => window.print();
    if (btnReload) btnReload.onclick = () => location.reload();
    if (!q) return;

    let last = new Set();
    function clear() {
      if (!dsNodes) return;
      dsNodes.update(Array.from(last).map(id => ({ id, borderWidth: 1, color: undefined })));
      last.clear();
    }

    q.addEventListener('keydown', (ev) => {
      if (ev.key !== 'Enter') return;
      const term = (q.value || '').trim().toLowerCase();
      clear();
      if (!term) return;

      const hits = dsNodes.get({
        filter: n => n.id.toLowerCase() === term || (n.label || '').toLowerCase().includes(term)
      });
      if (!hits.length) return;

      const ids = hits.map(h => h.id);
      last = new Set(ids);

      // esmaece os demais
      const all = dsNodes.get();
      dsNodes.update(all.map(n => ({ id: n.id, color: Object.assign({}, n.color || {}, { opacity: 0.15 }), borderWidth: 1 })));

      // destaca os achados
      ids.forEach(id => {
        const cur = dsNodes.get(id);
        const hi = Object.assign({}, cur.color || {}, { opacity: 1 });
        dsNodes.update({ id, color: hi, borderWidth: 4 });
      });

      net.focus(ids[0], { scale: 1.2, animation: { duration: 400 } });
    });
  }

  function render(data) {
    const container = document.getElementById('mynetwork');
    if (!container) return;

    const rawNodes = Array.isArray(data.nodes) ? data.nodes : [];
    const rawEdges = Array.isArray(data.edges) ? data.edges : [];
    const faccaoColorById = inferFaccaoColors(rawNodes);

    // Dedup nós por id
    const byId = new Map();
    for (const n of rawNodes) {
      if (!n || n.id == null) continue;
      const id = String(n.id);
      if (byId.has(id)) continue;
      const label = cleanLabel(n.label) || id;
      const group = String(n.group ?? n.faccao_id ?? n.type ?? '0');
      const value = (typeof n.size === 'number') ? n.size : undefined;
      const photo = n.photo_url && /^https?:\/\//i.test(n.photo_url) ? n.photo_url : null;
      const color = colorForNode(n, faccaoColorById);
      const base = { id, label, group, value, color, borderWidth: 1 };
      if (photo) { base.shape = 'circularImage'; base.image = photo; } else { base.shape = 'dot'; }
      byId.set(id, base);
    }
    const nodes = Array.from(byId.values());

    const nodeIds = new Set(nodes.map(n => n.id));
    const edges = rawEdges
      .filter(e => e && e.source != null && e.target != null && nodeIds.has(String(e.source)) && nodeIds.has(String(e.target)))
      .map(e => {
        const rel = e.relation || '';
        const style = edgeStyleFor(rel);
        return {
          from: String(e.source),
          to: String(e.target),
          value: (e.weight != null ? Number(e.weight) : 1.0),
          title: rel ? `${rel} (w=${e.weight ?? 1})` : `w=${e.weight ?? 1}`,
          width: 0.2, // ULTRA-FINO
          ...style,
          arrows: { to: { enabled: true, scaleFactor: 0.3 } }
        };
      });

    if (!nodes.length) {
      container.innerHTML = '<div style="display:flex;height:100%;align-items:center;justify-content:center;opacity:.85">Nenhum dado para exibir (nodes=0).</div>';
      return;
    }

    // Tamanho por grau se necessário
    if (!nodes.some(n => typeof n.value === 'number')) {
      const deg = degreeMap(nodes, edges);
      nodes.forEach(n => { const d = deg[n.id] || 0; n.value = 10 + Math.log(d + 1) * 8; });
    }

    let dsNodes, dsEdges;
    try { dsNodes = new vis.DataSet(nodes); dsEdges = new vis.DataSet(edges); }
    catch (err) { console.error(err); container.innerHTML = `<pre style="padding:12px">${String(err).replace(/</g,'&lt;')}</pre>`; return; }

    const options = {
      interaction: { hover: true, dragNodes: true, dragView: false, zoomView: true, multiselect: true, navigationButtons: true },
      manipulation: { enabled: false },
      physics: {
        enabled: true,
        stabilization: { enabled: true, iterations: 300 },
        barnesHut: { gravitationalConstant: -8000, centralGravity: 0.2, springLength: 120, springConstant: 0.04, avoidOverlap: 0.2 }
      },
      layout: { improvedLayout: true, randomSeed: 42 },
      nodes: { shape: 'dot', borderWidth: 1 },
      edges: { smooth: false, width: 0.2, color: { opacity: 0.65 }, arrows: { to: { enabled: true, scaleFactor: 0.3 } } }
    };

    const net = new vis.Network(container, { nodes: dsNodes, edges: dsEdges }, options);
    net.once('stabilizationIterationsDone', () => {
      net.fit({ animation: { duration: 300 } });
      net.setOptions({ physics: false }); // para o movimento
    });
    net.on('doubleClick', () => net.fit({ animation: { duration: 300 } }));

    attachToolbar(net, dsNodes);
  }

  function run() {
    const container = document.getElementById('mynetwork');
    if (!container) return;

    if (typeof vis === 'undefined') {
      container.innerHTML = '<div style="padding:12px">vis-network não carregou. Verifique CSP/CDN.</div>';
      return;
    }

    const source = container.getAttribute('data-source') || 'server';
    const endpoint = container.getAttribute('data-endpoint') || '/v1/graph/membros';

    if (source === 'server') {
      const tag = document.getElementById('__KG_DATA__');
      if (!tag) { container.innerHTML = '<div style="padding:12px">Bloco de dados ausente.</div>'; return; }
      try { render(JSON.parse(tag.textContent || '{}')); }
      catch (e) { console.error(e); container.innerHTML = '<pre>' + String(e).replace(/</g, '&lt;') + '</pre>'; }
    } else {
      const params = new URLSearchParams(window.location.search);
      const qs = new URLSearchParams();
      const fac = params.get('faccao_id'); if (fac && fac.trim() !== '') qs.set('faccao_id', fac.trim());
      qs.set('include_co', params.get('include_co') ?? 'true');
      qs.set('max_pairs', params.get('max_pairs') ?? '8000');
      qs.set('max_nodes', params.get('max_nodes') ?? '2000');
      qs.set('max_edges', params.get('max_edges') ?? '4000');
      qs.set('cache', params.get('cache') ?? 'false');
      const url = endpoint + '?' + qs.toString();
      fetch(url, { headers: { 'Accept': 'application/json' } })
        .then(async r => { if (!r.ok) throw new Error(r.status + ': ' + await r.text()); return r.json(); })
        .then(render)
        .catch(err => { console.error(err); container.innerHTML = '<pre>' + String(err).replace(/</g, '&lt;') + '</pre>'; });
    }
  }

  if (document.readyState !== 'loading') run();
  else document.addEventListener('DOMContentLoaded', run);
})();
