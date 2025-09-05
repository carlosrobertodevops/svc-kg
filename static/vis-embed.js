// =============================================================================
// Arquivo: static/vis-embed.js
// Versão: v1.7.20
// Objetivo: Renderizar /v1/vis/visjs (vis-network) sem “tela branca”
// Funções/métodos:
// - Espera o vis-network estar carregado (retry) e mostra erro útil se faltar
// - Respeita data-source="server" (usa __KG_DATA__) ou "client" (fetch do endpoint)
// - Dedup de nós/arestas; arestas e setas finas; física desativada após estabilizar
// - Busca com destaque + fit; cores CV/PCC/FUNÇÃO; arestas de função amarelas
// =============================================================================

(function () {
  const container = document.getElementById('mynetwork');
  if (!container) return;

  const COLOR_CV = '#d32f2f';
  const COLOR_PCC = '#0d47a1';
  const COLOR_FUNCAO = '#FBC02D';
  const COLOR_EDGE_DEFAULT = '#90a4ae';

  function hashColor(s) {
    s = String(s || 'x'); let h = 0;
    for (let i = 0; i < s.length; i++) { h = (h << 5) - h + s.charCodeAt(i); h |= 0; }
    const hue = Math.abs(h) % 360; return `hsl(${hue},70%,50%)`;
  }
  function cleanLabel(raw) {
    if (!raw) return '';
    const s = String(raw).trim();
    if (!(s.startsWith('{') && s.endsWith('}'))) return s;
    const inner = s.slice(1, -1);
    if (!inner) return '';
    return inner.replace(/(^|,)\s*"?null"?\s*(?=,|$)/gi, '')
      .replace(/"/g, '')
      .split(',').map(x => x.trim()).filter(Boolean).join(', ');
  }
  function inferFactionColors(rawNodes) {
    const colors = {};
    (rawNodes || []).filter(n => n && n.type === 'faccao').forEach(n => {
      const name = cleanLabel(n.label || '').toUpperCase();
      const id = String(n.id);
      if (name.includes('PCC')) colors[id] = COLOR_PCC;
      else if (name === 'CV' || name.includes('COMANDO VERMELHO')) colors[id] = COLOR_CV;
    });
    return colors;
  }
  function colorForNode(n, faccaoColorById) {
    if ((n.type || '').toLowerCase() === 'funcao') return COLOR_FUNCAO;
    const gid = String(n.group ?? n.faccao_id ?? '');
    if (gid && faccaoColorById[gid]) return faccaoColorById[gid];
    return hashColor(gid || n.type || 'x');
  }
  function edgeColorFor(rel) {
    const R = (rel || '').toUpperCase();
    return (R.includes('FUNCAO')) ? COLOR_FUNCAO : COLOR_EDGE_DEFAULT;
  }

  function buildFrom(raw) {
    const rawNodes = raw.nodes || [];
    const rawEdges = raw.edges || [];
    const faccaoColorById = inferFactionColors(rawNodes);

    const seen = new Set();
    const nodes = [];
    for (const n of rawNodes) {
      if (!n || n.id == null) continue;
      const id = String(n.id);
      if (seen.has(id)) continue;
      seen.add(id);
      const label = cleanLabel(n.label) || id;
      const group = String(n.group ?? n.faccao_id ?? n.type ?? '0');
      const value = (typeof n.size === 'number') ? n.size : undefined;
      const photo = n.photo_url && /^https?:\/\//i.test(n.photo_url) ? n.photo_url : null;
      const color = colorForNode(n, faccaoColorById);
      const base = { id, label, group, value, color, borderWidth: 1 };
      if (photo) { base.shape = 'circularImage'; base.image = photo; } else { base.shape = 'dot'; }
      nodes.push(base);
    }

    const nodeIds = new Set(nodes.map(n => n.id));
    const seenE = new Set();
    const edges = [];
    for (const e of (rawEdges || [])) {
      if (!e || e.source == null || e.target == null) continue;
      const from = String(e.source), to = String(e.target);
      if (!nodeIds.has(from) || !nodeIds.has(to)) continue;
      const rel = e.relation || '';
      const key = `${from}|${to}|${rel}`;
      if (seenE.has(key)) continue;
      seenE.add(key);
      edges.push({
        from, to,
        value: (e.weight != null ? Number(e.weight) : 1.0),
        width: 0.2,                        // ultrafino
        color: { color: edgeColorFor(rel) },
        arrows: { to: { enabled: true, scaleFactor: 0.4 } },
        title: rel ? `${rel} (w=${e.weight ?? 1})` : `w=${e.weight ?? 1}`
      });
    }
    return { nodes, edges };
  }

  function attachToolbar(network, dsNodes) {
    const q = document.getElementById('kg-search');
    const p = document.getElementById('btn-print');
    const r = document.getElementById('btn-reload');
    if (p) p.onclick = () => window.print();
    if (r) r.onclick = () => location.reload();
    if (!q) return;
    function colorObj(c, opacity){
      if (typeof c === 'object' && c) { return Object.assign({}, c, { opacity: opacity }); }
      return {
        background: c || '#90a4ae',
        border: c || '#90a4ae',
        highlight: { background: c || '#90a4ae', border: c || '#90a4ae' },
        hover: { background: c || '#90a4ae', border: c || '#90a4ae' },
        opacity: opacity
      };
    }
    function runSearch(txt){
      const t = (txt||'').trim().toLowerCase(); if (!t) return;
      const all = dsNodes.get();
      const hits = all.filter(n => (String(n.label||'').toLowerCase().includes(t)) || (String(n.id)===t));
      if (!hits.length) return;
      all.forEach(n => dsNodes.update({ id: n.id, color: colorObj(n.color, 0.25) }));
      hits.forEach(h => { const cur = dsNodes.get(h.id); dsNodes.update({ id: h.id, color: colorObj(cur.color, 1) }); });
      network.fit({ nodes: hits.map(h=>h.id), animation: { duration: 300 } });
    }
    q.addEventListener('change', () => runSearch(q.value));
    q.addEventListener('keyup', (e) => { if (e.key === 'Enter') runSearch(q.value); });
  }

  function render(raw) {
    const data = buildFrom(raw);
    if (!data.nodes.length) {
      container.innerHTML = '<div style="padding:12px">Nenhum dado para exibir.</div>';
      return;
    }
    const dsNodes = new vis.DataSet(data.nodes);
    const dsEdges = new vis.DataSet(data.edges);

    const options = {
      interaction: { hover: true, dragNodes: true, dragView: false, zoomView: true, multiselect: true, navigationButtons: true },
      manipulation: { enabled: false },
      physics: {
        enabled: true,
        stabilization: { enabled: true, iterations: 420 }
      },
      nodes: { shape: 'dot', borderWidth: 1 },
      edges: { smooth: false }
    };

    const net = new vis.Network(container, { nodes: dsNodes, edges: dsEdges }, options);
    net.once('stabilizationIterationsDone', () => net.setOptions({ physics: { enabled: false } }));
    attachToolbar(net, dsNodes);
  }

  // Espera 'vis' ficar disponível (evita tela branca se o bundle atrasar)
  function waitForVisAndRun(dataOrFn, tries = 20) {
    const go = () => {
      try {
        if (typeof vis === 'undefined') throw new Error('vis-network ainda não carregou');
        if (typeof dataOrFn === 'function') { dataOrFn(); } else { render(dataOrFn); }
      } catch (e) {
        if (tries <= 0) { container.innerHTML = '<pre>'+String(e).replace(/</g,'&lt;')+'</pre>'; return; }
        setTimeout(() => waitForVisAndRun(dataOrFn, tries-1), 150);
      }
    };
    go();
  }

  const source = container.getAttribute('data-source') || 'server';
  if (source === 'server') {
    const tag = document.getElementById('__KG_DATA__');
    if (!tag) { container.innerHTML = '<div style="padding:12px">Bloco de dados ausente (source=server).</div>'; return; }
    let data = {};
    try { data = JSON.parse(tag.textContent || '{}'); }
    catch (e) { container.innerHTML = '<pre>'+String(e).replace(/</g,'&lt;')+'</pre>'; return; }
    waitForVisAndRun(data);
  } else {
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
      .then(data => waitForVisAndRun(data))
      .catch(err => { container.innerHTML = '<pre>' + String(err).replace(/</g,'&lt;') + '</pre>'; });
  }
})();
