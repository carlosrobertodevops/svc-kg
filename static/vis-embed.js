// =============================================================================
// Arquivo: static/vis-embed.js
// Versão: v1.7.20
// Objetivo: Script de incorporação da visualização vis-network no endpoint /v1/vis/visjs
// Funções/métodos:
// - loadServerOrFetch(): usa dados incorporados quando data-source="server", ou busca o /v1/graph/membros
// - sanitizeGraph(): normaliza/deduplica nós/arestas e aplica mapeamento de cores (CV, PCC, funções)
// - renderVis(): cria DataSets, configura opções (arestas finas), busca, destaque e física desligada pós-estabilização
// - attachToolbar(): imprime/recarrega e executa a busca com destaque/fit
// =============================================================================
(function () {
  const container = document.getElementById('mynetwork');
  if (!container) return;

  // ---------- Constantes de cor ----------
  const COLOR_CV   = '#d32f2f'; // vermelho
  const COLOR_PCC  = '#0d47a1'; // azul escuro
  const COLOR_FUNC = '#fdd835'; // amarelo p/ nós de função
  const EDGE_COLORS = {
    'PERTENCE_A': '#9e9e9e',
    'EXERCE': '#00796b',
    'FUNCAO_DA_FACCAO': '#fdd835', // amarelo para arestas de função
    'CO_FACCAO': '#8e24aa',
    'CO_FUNCAO': '#546e7a'
  };

  // ---------- Utilidades ----------
  function hashColor(s) {
    s = String(s || ''); let h = 0;
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
    return inner.replace(/(^|,)\s*"?null"?\s*(?=,|$)/gi, '')
      .replace(/"/g, '')
      .split(',').map(x => x.trim()).filter(Boolean).join(', ');
  }
  function degreeMap(nodes, edges) {
    const d = {}; nodes.forEach(n => d[n.id] = 0);
    edges.forEach(e => { if (e.from in d) d[e.from]++; if (e.to in d) d[e.to]++; });
    return d;
  }

  // Determina mapeamento de cores por facção (id da facção -> cor)
  function inferFaccaoColors(rawNodes) {
    const map = {};
    rawNodes.filter(n => n && (n.type === 'faccao' || String(n.type).toLowerCase() === 'faccao')).forEach(n => {
      const name = cleanLabel(n.label || '').toUpperCase();
      const id = String(n.id);
      if (!name) return;
      if (name.includes('PCC')) map[id] = COLOR_PCC;
      else if (name === 'CV' || name.includes('COMANDO VERMELHO')) map[id] = COLOR_CV;
    });
    return map;
  }

  // ---------- Sanitização e deduplicação ----------
  function sanitizeGraph(raw) {
    const rawNodes = (raw && raw.nodes) ? raw.nodes : [];
    const rawEdges = (raw && raw.edges) ? raw.edges : [];

    // Dedup de nós por id (mantém foto, maior size)
    const byId = {};
    for (const n of rawNodes) {
      if (!n || n.id == null) continue;
      const id = String(n.id);
      const label = cleanLabel(n.label || id);
      const next = { ...n, id, label };
      if (!byId[id]) byId[id] = next;
      else {
        if (!byId[id].photo_url && next.photo_url) byId[id].photo_url = next.photo_url;
        const s1 = Number(byId[id].size || 0), s2 = Number(next.size || 0);
        if (s2 > s1) byId[id].size = s2;
        for (const k of ['group', 'faccao_id', 'type']) {
          if (!byId[id][k] && next[k]) byId[id][k] = next[k];
        }
      }
    }
    const nodes = Object.values(byId);

    // Mapa de cor por facção e cor por nó
    const faccaoColorById = inferFaccaoColors(nodes);
    const nodesStyled = nodes.map(n => {
      const group = String(n.group ?? n.faccao_id ?? n.type ?? '0');
      // Se nó é função, força amarelo
      const isFunc = (String(n.type || '').toLowerCase() === 'funcao' || /fun[cç][aã]o/i.test(String(n.label || '')));
      let color = isFunc ? COLOR_FUNC : (faccaoColorById[group] || hashColor(group || (n.type || 'x')));
      const photo = n.photo_url && /^https?:\/\//i.test(n.photo_url) ? n.photo_url : null;
      const base = { id: String(n.id), label: n.label || String(n.id), borderWidth: 1, color };
      if (typeof n.size === 'number') base.value = n.size;
      if (photo) { base.shape = 'circularImage'; base.image = photo; }
      else { base.shape = 'dot'; }
      return base;
    });

    // Dedup de arestas por (from,to,relation)
    const nodeSet = new Set(nodesStyled.map(n => n.id));
    const seen = new Set();
    const edges = [];
    for (const e of rawEdges) {
      if (!e) continue;
      const from = String(e.source ?? e.from ?? '');
      const to = String(e.target ?? e.to ?? '');
      if (!nodeSet.has(from) || !nodeSet.has(to)) continue;
      const rel = String(e.relation || '');
      const key = `${from}|${to}|${rel}`;
      if (seen.has(key)) continue;
      seen.add(key);

      const baseColor = EDGE_COLORS[rel] || '#90a4ae';
      edges.push({
        from, to,
        value: (e.weight != null ? Number(e.weight) : 1.0),
        title: rel ? `${rel} (w=${e.weight ?? 1})` : `w=${e.weight ?? 1}`,
        width: 1, // arestas finas
        color: baseColor
      });
    }

    // Se nenhum value, usa grau como proxy (explode os mais conectados)
    const hasValue = nodesStyled.some(n => typeof n.value === 'number');
    if (!hasValue) {
      const deg = degreeMap(nodesStyled, edges);
      nodesStyled.forEach(n => { const d = deg[n.id] || 0; n.value = 10 + Math.log(d + 1) * 8; });
    }

    return { nodes: nodesStyled, edges };
  }

  // ---------- Toolbar (print/reload/busca) ----------
  function colorWithOpacity(c, opacity) {
    if (typeof c === 'object' && c) return Object.assign({}, c, { opacity });
    return {
      background: c || '#90a4ae',
      border: c || '#90a4ae',
      highlight: { background: c || '#90a4ae', border: c || '#90a4ae' },
      hover: { background: c || '#90a4ae', border: c || '#90a4ae' },
      opacity
    };
  }

  function attachToolbar(network, dsNodes) {
    const q = document.getElementById('kg-search');
    const p = document.getElementById('btn-print');
    const r = document.getElementById('btn-reload');

    if (p) p.onclick = () => window.print();
    if (r) r.onclick = () => location.reload();

    function runSearch(txt) {
      const t = (txt || '').trim().toLowerCase();
      if (!t) return;
      const all = dsNodes.get();
      const hits = all.filter(n => String(n.label || '').toLowerCase().includes(t) || String(n.id) === t);
      if (!hits.length) return;

      // esmaece todos
      all.forEach(n => dsNodes.update({ id: n.id, color: colorWithOpacity(n.color, 0.25) }));
      // destaca hits
      hits.forEach(h => {
        const cur = dsNodes.get(h.id);
        dsNodes.update({ id: h.id, color: colorWithOpacity(cur.color, 1) });
      });
      // foco com leve zoom (efeito de “puxar” visual)
      network.fit({ nodes: hits.map(h => h.id), animation: { duration: 300 } });
    }

    if (q) {
      q.addEventListener('change', () => runSearch(q.value));
      q.addEventListener('keyup', (e) => { if (e.key === 'Enter') runSearch(q.value); });
    }
  }

  // ---------- Renderização ----------
  function renderVis(data) {
    if (typeof vis === 'undefined') {
      container.innerHTML = '<div style="padding:12px">vis-network não carregou. Verifique CSP/CDN.</div>';
      return;
    }

    const { nodes, edges } = sanitizeGraph(data);
    if (!nodes.length) {
      container.innerHTML = '<div style="display:flex;height:100%;align-items:center;justify-content:center;opacity:.85">Nenhum dado para exibir (nodes=0).</div>';
      return;
    }

    const dsNodes = new vis.DataSet(nodes);
    const dsEdges = new vis.DataSet(edges);

    const options = {
      interaction: {
        hover: true,
        dragNodes: true,    // arrastar nó não move o resto quando physics=false
        dragView: false,    // não arrasta a câmera
        zoomView: true,
        multiselect: true,
        navigationButtons: true
      },
      manipulation: { enabled: false },
      physics: {
        enabled: true,
        stabilization: { enabled: true, iterations: 300 },
        barnesHut: {
          gravitationalConstant: -12000,
          centralGravity: 0.25,
          springLength: 140,
          springConstant: 0.035,
          avoidOverlap: 0.3
        }
      },
      nodes: { shape: 'dot', borderWidth: 1 },
      edges: { smooth: false } // arestas retas e finas
    };

    const network = new vis.Network(container, { nodes: dsNodes, edges: dsEdges }, options);

    // Depois de estabilizar, desliga physics p/ que arraste só o nó selecionado
    network.once('stabilizationIterationsDone', () => {
      network.setOptions({ physics: false });
      network.fit({ animation: { duration: 300 } });
    });

    // Duplo clique para refazer o fit rapidamente
    network.on('doubleClick', () => network.fit({ animation: { duration: 300 } }));

    attachToolbar(network, dsNodes);
  }

  // ---------- Carregamento (server vs client) ----------
  async function loadServerOrFetch() {
    const source = container.getAttribute('data-source') || 'server';
    const endpoint = container.getAttribute('data-endpoint') || '/v1/graph/membros';

    if (source === 'server') {
      // usa bloco incorporado (evita buscar de novo e erros de itens duplicados)
      const tag = document.getElementById('__KG_DATA__');
      if (!tag) { container.innerHTML = '<div style="padding:12px">Bloco de dados ausente.</div>'; return; }
      try {
        const json = JSON.parse(tag.textContent || '{}');
        renderVis(json);
      } catch (e) {
        console.error(e);
        container.innerHTML = '<pre>' + String(e).replace(/</g, '&lt;') + '</pre>';
      }
      return;
    }

    // Busca do servidor
    const params = new URLSearchParams(window.location.search);
    const qs = new URLSearchParams();
    const fac = params.get('faccao_id'); if (fac && fac.trim() !== '') qs.set('faccao_id', fac.trim());
    qs.set('include_co', params.get('include_co') ?? 'true');
    qs.set('max_pairs', params.get('max_pairs') ?? '8000');
    qs.set('max_nodes', params.get('max_nodes') ?? '2000');
    qs.set('max_edges', params.get('max_edges') ?? '4000');
    qs.set('cache', params.get('cache') ?? 'false');

    const url = endpoint + '?' + qs.toString();
    try {
      const r = await fetch(url, { headers: { 'Accept': 'application/json' } });
      if (!r.ok) throw new Error(r.status + ': ' + (await r.text()));
      const json = await r.json();
      renderVis(json);
    } catch (err) {
      console.error(err);
      container.innerHTML = '<pre>' + String(err).replace(/</g, '&lt;') + '</pre>';
    }
  }

  if (document.readyState !== 'loading') loadServerOrFetch();
  else document.addEventListener('DOMContentLoaded', loadServerOrFetch);
})();
