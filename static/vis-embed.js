// =============================================================================
// Arquivo: static/vis-embed.js
// Versão: v1.7.20
// Objetivo: Renderização cliente do vis-network para /v1/vis/visjs
// Funções/métodos:
// - Carrega dados embutidos (data-source="server") ou busca do endpoint (client)
// - Dedup no cliente; mapeia source/target -> from/to
// - Cores: CV (vermelho), PCC (azul escuro), funções (amarelo)
// - Arestas muito finas (width baixo + opacidade); setas pequenas
// - Física só na estabilização (depois OFF); arrastar move apenas o nó
// - Busca: esmaece todos, destaca hits (borda grossa + aumenta valor) e foca
// - Explode nós com maior grau (value proporcional ao grau)
// =============================================================================
(function () {
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

  function okUrl(u) { return typeof u === 'string' && /^https?:\/\//i.test(u); }
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
  function hashColor(s) {
    s = String(s || ''); let h = 0;
    for (let i = 0; i < s.length; i++) { h = (h << 5) - h + s.charCodeAt(i); h |= 0; }
    const hue = Math.abs(h) % 360; return `hsl(${hue},70%,50%)`;
  }

  function inferFaccaoColors(rawNodes) {
    const facById = {};
    rawNodes.filter(n => n && n.type === 'faccao').forEach(n => {
      const name = cleanLabel(n.label || '').toUpperCase();
      const id = String(n.id);
      if (!name) return;
      if (name.includes('PCC')) facById[id] = COLOR_PCC;
      else if (name === 'CV' || name.includes('COMANDO VERMELHO')) facById[id] = COLOR_CV;
    });
    return facById;
  }

  function colorForNode(n, faccaoColorById) {
    if ((n.type || '').toLowerCase() === 'funcao' || String(n.group || '') === '6') return COLOR_FUNCAO;
    const gid = String(n.group ?? n.faccao_id ?? '');
    if (gid && faccaoColorById[gid]) return faccaoColorById[gid];
    return hashColor(gid || (n.type || 'x'));
  }

  function degreeMap(nodes, edges) {
    const d = {}; nodes.forEach(n => d[n.id] = 0);
    edges.forEach(e => { if (e.from in d) d[e.from]++; if (e.to in d) d[e.to]++; });
    return d;
  }

  function toVisData(raw) {
    const rawNodes = (raw && raw.nodes) ? raw.nodes : [];
    const rawEdges = (raw && raw.edges) ? raw.edges : [];
    const faccaoColorById = inferFaccaoColors(rawNodes);

    const nodes = rawNodes
      .filter(n => n && n.id != null)
      .map(n => {
        const id = String(n.id);
        const label = cleanLabel(n.label) || id;
        const group = String(n.group ?? n.faccao_id ?? n.type ?? '0');
        const value = (typeof n.size === 'number') ? n.size : undefined;
        const photo = okUrl(n.photo_url) ? n.photo_url : null;
        const type = n.type || '';
        const color = colorForNode({ group, type }, faccaoColorById);
        const base = { id, label, group, value, color, borderWidth: 1 };
        if (photo) { base.shape = 'circularImage'; base.image = photo; } else { base.shape = 'dot'; }
        return base;
      });

    const idSet = new Set(nodes.map(n => n.id));
    const edges = rawEdges
      .filter(e => e && e.source != null && e.target != null && idSet.has(String(e.source)) && idSet.has(String(e.target)))
      .map(e => {
        const rel = e.relation || '';
        const from = String(e.source), to = String(e.target);
        const baseColor = (rel && rel.toUpperCase().includes('FUNCAO')) ? COLOR_FUNCAO : (EDGE_COLORS[rel] || '#90a4ae');
        return {
          from, to,
          value: (e.weight != null ? Number(e.weight) : 1.0),
          title: rel ? `${rel} (w=${e.weight ?? 1})` : `w=${e.weight ?? 1}`,
          width: 0.25,
          color: { color: baseColor, opacity: 0.45 },
          arrows: { to: { enabled: true, scaleFactor: 0.3 } }
        };
      });

    const deg = degreeMap(nodes, edges);
    nodes.forEach(n => {
      if (typeof n.value !== 'number') {
        const d = deg[n.id] || 0;
        n.value = 10 + Math.pow(d, 0.6) * 8;
      }
    });

    return { nodes, edges };
  }

  function toOpacity(color, opacity) {
    if (typeof color === 'object' && color) return Object.assign({}, color, { opacity: opacity });
    return {
      background: color || '#90a4ae',
      border: color || '#90a4ae',
      highlight: { background: color || '#90a4ae', border: color || '#90a4ae' },
      hover: { background: color || '#90a4ae', border: color || '#90a4ae' },
      opacity: opacity
    };
  }

  function attachToolbar(network, dsNodes) {
    const q = document.getElementById('kg-search');
    const p = document.getElementById('btn-print');
    const r = document.getElementById('btn-reload');
    if (p) p.onclick = () => window.print();
    if (r) r.onclick = () => location.reload();
    if (q) {
      const run = () => {
        const text = (q.value || '').trim().toLowerCase();
        if (!text) return;
        const all = dsNodes.get();
        const hits = all.filter(n => (String(n.label || '').toLowerCase().includes(text)) || (String(n.id).toLowerCase() === text));
        if (!hits.length) return;
        all.forEach(n => dsNodes.update({ id: n.id, color: toOpacity(n.color, 0.15), borderWidth: 1 }));
        hits.forEach(h => dsNodes.update({ id: h.id, color: toOpacity(h.color, 1), borderWidth: 4, value: (h.value || 12) + 6 }));
        network.focus(hits[0].id, { scale: 1.25, animation: { duration: 400 } });
      };
      q.addEventListener('keydown', e => { if (e.key === 'Enter') run(); });
    }
  }

  function render(raw) {
    if (typeof vis === 'undefined' || !vis.Network) {
      container.innerHTML = '<div style="padding:12px">vis-network não carregou. Verifique /static/vendor/vis-network.min.js</div>';
      return;
    }
    const data = toVisData(raw);
    if (!data.nodes.length) {
      container.innerHTML = '<div style="display:flex;height:100%;align-items:center;justify-content:center;opacity:.85">Nenhum dado para exibir (nodes=0).</div>';
      return;
    }
    const dsNodes = new vis.DataSet(data.nodes);
    const dsEdges = new vis.DataSet(data.edges);

    const options = {
      interaction: {
        hover: true,
        dragNodes: true,
        dragView: false,
        zoomView: true,
        multiselect: true,
        navigationButtons: true
      },
      manipulation: { enabled: false },
      physics: {
        enabled: true,
        stabilization: { enabled: true, iterations: 600 },
        barnesHut: {
          gravitationalConstant: -18000,
          centralGravity: 0.2,
          springLength: 180,
          springConstant: 0.04,
          avoidOverlap: 1.2
        }
      },
      layout: { improvedLayout: true, randomSeed: 42 },
      nodes: { shape: 'dot', borderWidth: 1, scaling: { min: 8, max: 42 } },
      edges: { smooth: false }
    };

    const network = new vis.Network(container, { nodes: dsNodes, edges: dsEdges }, options);
    network.once('stabilizationIterationsDone', () => {
      network.fit({ animation: { duration: 300 } });
      network.setOptions({
        edges: {
          width: 0.25,
          color: { opacity: 0.45 },
          arrows: { to: { enabled: true, scaleFactor: 0.3 } }
        },
        physics: false
      });
    });
    network.on('doubleClick', () => network.fit({ animation: { duration: 300 } }));

    attachToolbar(network, dsNodes);
  }

  const source = container.getAttribute('data-source') || 'server';
  if (source === 'server') {
    const tag = document.getElementById('__KG_DATA__');
    if (!tag) {
      container.innerHTML = '<div style="padding:12px">Bloco de dados embutido ausente.</div>';
      return;
    }
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

    const endpoint = (container.getAttribute('data-endpoint') || '/v1/graph/membros') + '?' + qs.toString();
    fetch(endpoint, { headers: { 'Accept': 'application/json' } })
      .then(async r => { if (!r.ok) throw new Error(r.status + ': ' + await r.text()); return r.json(); })
      .then(render)
      .catch(err => { console.error(err); container.innerHTML = '<pre>' + String(err).replace(/</g, '&lt;') + '</pre>'; });
  }
})();
