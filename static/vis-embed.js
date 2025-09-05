// static/vis-embed.js
(function () {
  if (!document.currentScript) return;
  const container = document.getElementById('mynetwork');
  if (!container) return;

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
    s = String(s || ''); let h = 0;
    for (let i = 0; i < s.length; i++) { h = (h << 5) - h + s.charCodeAt(i); h |= 0; }
    const hue = Math.abs(h) % 360; return `hsl(${hue},70%,50%)`;
  }
  function isPgTextArray(s) { s = (s || '').trim(); return s.length >= 2 && s[0] == '{' && s[s.length - 1] == '}'; }
  function cleanLabel(raw) {
    if (!raw) return '';
    const s = String(raw).trim();
    if (!isPgTextArray(s)) return s;
    const inner = s.slice(1, -1);
    if (!inner) return '';
    return inner.replace(/(^|,)\s*"?null"?\s*(?=,|$)/gi, '')
      .replace(/"/g, '')
      .split(',').map(x => x.trim()).filter(Boolean).join(', ');
  }
  function degreeMap(nodes, edges) {
    const d = {}; nodes.forEach(n => d[n.id] = 0);
    edges.forEach(e => { if (e.from in d) d[e.from]++; if (e.to in d) d[e.to]++; });
    return d;
  }
  function inferFaccaoColors(rawNodes) {
    const map = {};
    rawNodes.filter(n => n && n.type === 'faccao').forEach(n => {
      const name = cleanLabel(n.label || '').toUpperCase();
      const id = String(n.id);
      if (!name) return;
      if (name.includes('PCC')) map[id] = COLOR_PCC;
      else if (name === 'CV' || name.includes('COMANDO VERMELHO')) map[id] = COLOR_CV;
    });
    return map;
  }
  function colorForNode(n, faccaoColorById) {
    if ((n.type || '').toLowerCase().startsWith('funcao')) return COLOR_FUNCAO;
    const gid = String(n.group ?? n.faccao_id ?? '');
    if (gid && faccaoColorById[gid]) return faccaoColorById[gid];
    return hashColor(gid || (n.type || 'x'));
  }
  function edgeStyleFor(relation) {
    const base = EDGE_COLORS[relation] || '#90a4ae';
    return { color: base };
  }
  function attachToolbar(net, dsNodes) {
    const q = document.getElementById('kg-search');
    const btnPrint = document.getElementById('btn-print');
    const btnReload = document.getElementById('btn-reload');
    if (btnPrint) btnPrint.onclick = () => window.print();
    if (btnReload) btnReload.onclick = () => location.reload();
    if (q) {
      const run = () => selectByQuery(net, dsNodes, q.value);
      q.addEventListener('change', run);
      q.addEventListener('keyup', (e) => { if (e.key === 'Enter') run(); });
    }
  }
  function selectByQuery(net, dsNodes, query) {
    const text = (query || '').trim().toLowerCase();
    if (!text) return;
    const all = dsNodes.get();
    const hits = all.filter(n => (n.label || '').toLowerCase().includes(text) || String(n.id) === text);
    if (!hits.length) return;

    dsNodes.update(all.map(n => Object.assign(n, { color: Object.assign({}, n.color, { opacity: 0.2 }), borderWidth: 1 })));

    const id = hits[0].id;
    const cur = dsNodes.get(id);
    const hi = Object.assign({}, cur.color || {}, { opacity: 1 });
    dsNodes.update({ id, color: hi, borderWidth: 3, value: (cur.value || 12) * 1.2 });

    net.focus(id, { nodes: [id], animation: { duration: 300 }, scale: 1.0 });
    const pos = net.getPositions([id])[id];
    if (pos) { net.moveNode(id, pos.x + 80, pos.y - 60); }
  }

  const params = new URLSearchParams(window.location.search);
  const qs = new URLSearchParams();
  const fac = params.get('faccao_id'); if (fac && fac.trim() !== '') qs.set('faccao_id', fac.trim());
  qs.set('include_co', params.get('include_co') ?? 'true');
  qs.set('max_pairs', params.get('max_pairs') ?? '8000');
  qs.set('max_nodes', params.get('max_nodes') ?? '2000');
  qs.set('max_edges', params.get('max_edges') ?? '4000');
  qs.set('cache', params.get('cache') ?? 'false');

  const endpoint = (container.getAttribute('data-endpoint') || '/v1/graph/membros') + '?' + qs.toString();
  fetch(endpoint, { headers: { 'Accept': 'application/json' } })
    .then(async r => { if (!r.ok) throw new Error(r.status + ': ' + await r.text()); return r.json(); })
    .then(function render(data) {
      const rawNodes = data.nodes || [];
      const rawEdges = data.edges || [];

      const faccaoColorById = inferFaccaoColors(rawNodes);

      const nodes = rawNodes
        .filter(n => n && n.id != null)
        .map(n => {
          const id = String(n.id);
          const label = cleanLabel(n.label) || id;
          const group = String(n.group ?? n.faccao_id ?? n.type ?? '0');
          const value = (typeof n.size === 'number') ? n.size : undefined;
          const photo = n.photo_url && /^https?:\/\//i.test(n.photo_url) ? n.photo_url : null;
          const color = colorForNode(n, faccaoColorById);
          const base = { id, label, group, value, color, borderWidth: 1 };
          if (photo) { base.shape = 'circularImage'; base.image = photo; } else { base.shape = 'dot'; }
          return base;
        });

      const nodeIds = new Set(nodes.map(n => n.id));
      const edges = rawEdges
        .filter(e => e && e.source != null && e.target != null && nodeIds.has(String(e.source)) && nodeIds.has(String(e.target)))
        .map(e => {
          const rel = e.relation || '';
          const style = edgeStyleFor(rel);
          return { from: String(e.source), to: String(e.target), value: (e.weight != null ? Number(e.weight) : 1.0), title: rel ? `${rel} (w=${e.weight ?? 1})` : `w=${e.weight ?? 1}`, width: 1, color: style };
        });

      const dsNodes = new vis.DataSet(nodes);
      const dsEdges = new vis.DataSet(edges);

      const options = {
        interaction: { hover: true, dragNodes: true, dragView: false, zoomView: true, multiselect: true, navigationButtons: true },
        manipulation: { enabled: false },
        physics: { enabled: false },
        layout: { improvedLayout: true, randomSeed: 42 },
        nodes: { shape: 'dot', borderWidth: 1 },
        edges: { smooth: false, width: 1 }
      };

      const net = new vis.Network(container, { nodes: dsNodes, edges: dsEdges }, options);
      attachToolbar(net, dsNodes);
    })
    .catch(err => { console.error(err); container.innerHTML = '<pre>' + String(err).replace(/</g, '&lt;') + '</pre>'; });
})();
