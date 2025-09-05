// =============================================================================
// Arquivo: static/vis-embed.js
// Versão: v1.7.20
// Objetivo: Helper para vis-network quando usado em modo "client-side"
// Funções/métodos:
// - Renderização client-side, busca com destaque + bring-to-center, arestas finas
// - Congelar física após estabilização (só nó arrastado se move)
// - Cores: CV (vermelho), PCC (azul), funções (amarelo), fotos para membros
// =============================================================================
(function () {
  if (!document.currentScript) return;
  const container = document.getElementById('mynetwork');
  if (!container) return;

  const COLOR_CV = '#d32f2f';
  const COLOR_PCC = '#0d47a1';
  const COLOR_ROLE = '#fbc02d';
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
  function isRoleNode(n){
    const t=(n.type||'').toString().toLowerCase();
    return ['funcao','função','funcao_da_faccao','role','cargo'].includes(t);
  }
  function colorForNode(n, faccaoColorById) {
    if (isRoleNode(n)) return COLOR_ROLE;
    const gid = String(n.group ?? n.faccao_id ?? '');
    if (gid && faccaoColorById[gid]) return faccaoColorById[gid];
    return hashColor(gid || (n.type || 'x'));
  }
  function edgeStyleFor(relation) {
    const base = EDGE_COLORS[relation] || '#90a4ae';
    return { color: base, width: 1 };
  }

  function selectByQuery(net, dsNodes, query) {
    const text = (query || '').trim().toLowerCase();
    if (!text) return;
    const all = dsNodes.get();
    const hit = all.find(n => (n.label || '').toLowerCase().includes(text) || String(n.id) === text);
    if (!hit) return;

    // opacidade
    dsNodes.update(all.map(n => Object.assign({}, n, { color: Object.assign({}, n.color, { opacity: 0.25 }) })));
    const cur = dsNodes.get(hit.id);
    const hi = Object.assign({}, cur.color || {}, { opacity: 1 });
    dsNodes.update({ id: hit.id, color: hi });

    // traz para o centro
    const view = net.getViewPosition();
    net.moveNode(hit.id, view.x, view.y);
    net.selectNodes([hit.id], false);
    net.focus(hit.id, { animation: { duration: 300 } });
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
          const color = colorForNode({ group, type: n.type }, faccaoColorById);
          const base = { id, label, group, value, color, borderWidth: 1 };
          if (photo) { base.shape = 'circularImage'; base.image = photo; }
          else { base.shape = 'dot'; }
          return base;
        });

      const nodeIds = new Set(nodes.map(n => n.id));
      const edges = rawEdges
        .filter(e => e && e.source != null && e.target != null && nodeIds.has(String(e.source)) && nodeIds.has(String(e.target)))
        .map(e => {
          const rel = e.relation || '';
          const style = edgeStyleFor(rel);
          return {
            from: String(e.source),
            to: String(e.target),
            title: rel,
            width: 1,
            color: style.color,
            arrows: { to: { enabled: true, scaleFactor: 0.6 } }
          };
        });

      if (!nodes.length) {
        container.innerHTML = '<div style="display:flex;height:100%;align-items:center;justify-content:center;opacity:.85">Nenhum dado para exibir (nodes=0).</div>';
        return;
      }

      const hasSize = nodes.some(n => typeof n.value === 'number');
      if (!hasSize) {
        const deg = degreeMap(nodes, edges);
        nodes.forEach(n => { const d = deg[n.id] || 0; n.value = 10 + Math.log(d + 1) * 8; });
      }

      const dsNodes = new vis.DataSet(nodes);
      const dsEdges = new vis.DataSet(edges);

      const options = {
        interaction: { hover: true, dragNodes: true, dragView: false, zoomView: true, multiselect: true, navigationButtons: true },
        manipulation: { enabled: false },
        physics: {
          enabled: true,
          stabilization: { enabled: true, iterations: 250 },
          barnesHut: { gravitationalConstant: -8000, centralGravity: 0.2, springLength: 120, springConstant: 0.04, avoidOverlap: 0.2 }
        },
        nodes: { shape: 'dot', borderWidth: 1 },
        edges: { smooth: false, width: 1, scaling: { min: 1, max: 1 } }
      };

      const net = new vis.Network(container, { nodes: dsNodes, edges: dsEdges }, options);
      net.once('stabilizationIterationsDone', () => { net.setOptions({ physics: false }); net.fit({ animation: { duration: 300 } }); });
      net.on('doubleClick', () => net.fit({ animation: { duration: 300 } }));

      // toolbar (se existir)
      const q = document.getElementById('kg-search');
      const p = document.getElementById('btn-print');
      const r = document.getElementById('btn-reload');
      if (p) p.onclick = () => window.print();
      if (r) r.onclick = () => location.reload();
      if (q) {
        const run = () => selectByQuery(net, dsNodes, q.value);
        q.addEventListener('change', run);
        q.addEventListener('keyup', (e) => { if (e.key === 'Enter') run(); });
      }
    })
    .catch(err => { console.error(err); container.innerHTML = '<pre>' + String(err).replace(/</g, '&lt;') + '</pre>'; });
})();
