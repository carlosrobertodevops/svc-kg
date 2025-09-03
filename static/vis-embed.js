// static/vis-embed.js (v1.1) — suporta 'server' (embed) e 'client' (fetch) + limpeza de labels
(function () {
  const container = document.getElementById('mynetwork');
  if (!container) return;
  const source = container.getAttribute('data-source') || 'server';
  const endpoint = container.getAttribute('data-endpoint') || '/v1/graph/membros';
  const debug = container.getAttribute('data-debug') === 'true';

  const params = new URLSearchParams(window.location.search);
  const qs = new URLSearchParams();
  const faccao = params.get('faccao_id');
  if (faccao && faccao.trim() !== '') qs.set('faccao_id', faccao.trim());
  qs.set('include_co', params.get('include_co') ?? 'true');
  qs.set('max_pairs', params.get('max_pairs') ?? '8000');
  qs.set('max_nodes', params.get('max_nodes') ?? '2000');
  qs.set('max_edges', params.get('max_edges') ?? '4000');
  qs.set('cache', params.get('cache') ?? (source === 'server' ? 'true' : 'false'));
  const url = endpoint + '?' + qs.toString();

  const hashColor = (str) => {
    let h = 0; const s = String(str || '');
    for (let i = 0; i < s.length; i++) { h = (h << 5) - h + s.charCodeAt(i); h |= 0; }
    const hue = Math.abs(h) % 360; return `hsl(${hue},70%,50%)`;
  };

  const isPgTextArray = (s) => {
    s = (s || '').trim();
    return s.length >= 2 && s[0] === '{' && s[s.length - 1] === '}';
  };
  const cleanLabel = (raw) => {
    if (!raw) return '';
    const s = String(raw).trim();
    if (!isPgTextArray(s)) return s;
    const inner = s.slice(1, -1);
    if (!inner) return '';
    return inner.replace(/(^|,)\s*"?null"?\s*(?=,|$)/gi, '')
                .replace(/"/g, '')
                .split(',')
                .map(x => x.trim())
                .filter(Boolean)
                .join(', ');
  };

  const degreeMap = (nodes, edges) => {
    const d = Object.create(null);
    nodes.forEach(n => d[n.id] = 0);
    edges.forEach(e => { if (e.from in d) d[e.from]++; if (e.to in d) d[e.to]++; });
    return d;
  };

  const showEmpty = (msg) => {
    container.innerHTML = `
      <div style="display:flex;height:100%;align-items:center;justify-content:center;">
        <div style="opacity:.9;text-align:center">
          <div style="font-size:18px;margin-bottom:8px">${msg}</div>
          <div style="font-size:12px;color:#888">URL: ${url}</div>
        </div>
      </div>`;
  };

  const attachToolbar = (network, nodesCount, edgesCount) => {
    const btnPrint = document.getElementById('btn-print');
    const btnReload = document.getElementById('btn-reload');
    if (btnPrint) btnPrint.addEventListener('click', () => window.print());
    if (btnReload) btnReload.addEventListener('click', () => window.location.reload());
    const badge = document.getElementById('badge');
    if (badge && debug) badge.textContent = `nodes: ${nodesCount} · edges: ${edgesCount}`;
  };

  const render = (data) => {
    const nodesRaw = (data.nodes || []).filter(n => n && n.id);
    const edgesRaw = (data.edges || []).filter(e => e && e.source && e.target);

    const nodes = nodesRaw.map(n => ({ ...n, label: cleanLabel(n.label) || String(n.id) }));

    if (!nodes.length) { showEmpty('Nenhum dado para exibir (nodes=0).'); return; }

    const nodesVis = nodes.map(n => ({
      id: String(n.id),
      label: n.label || String(n.id),
      group: String(n.group ?? n.type ?? '0'),
      value: n.size ? Number(n.size) : undefined,
      color: hashColor(n.group ?? n.type ?? '0'),
      shape: 'dot'
    }));
    const edgesVis = edgesRaw.map(e => ({
      from: String(e.source),
      to: String(e.target),
      value: e.weight != null ? Number(e.weight) : 1.0,
      title: e.relation ? `${e.relation} (w=${e.weight ?? 1})` : `w=${e.weight ?? 1}`
    }));

    const hasSize = nodesVis.some(n => typeof n.value === 'number');
    if (!hasSize) {
      const deg = degreeMap(nodesVis, edgesVis);
      nodesVis.forEach(n => { const d = deg[n.id] || 0; n.value = 10 + Math.log(d + 1) * 8; });
    }

    const nodesDS = new vis.DataSet(nodesVis);
    const edgesDS = new vis.DataSet(edgesVis);

    const options = {
      interaction: { hover: true, dragNodes: true, dragView: true, zoomView: true, multiselect: true, navigationButtons: true },
      manipulation: { enabled: false },
      physics: {
        enabled: true, stabilization: { enabled: true, iterations: 500 },
        barnesHut: { gravitationalConstant: -8000, centralGravity: 0.2, springLength: 120, springConstant: 0.04, avoidOverlap: 0.2 }
      },
      nodes: { borderWidth: 1, shape: 'dot' },
      edges: { smooth: false, arrows: { to: { enabled: true } } }
    };

    const network = new vis.Network(container, { nodes: nodesDS, edges: edgesDS }, options);
    network.once('stabilizationIterationsDone', () => network.fit({ animation: { duration: 300 } }));
    network.on('doubleClick', () => network.fit({ animation: { duration: 300 } }));
    attachToolbar(network, nodesVis.length, edgesVis.length);
  };

  if (source === 'server') {
    const tag = document.getElementById('__KG_DATA__');
    if (!tag) { showEmpty('Sem bloco de dados embedado.'); return; }
    try {
      const data = JSON.parse(tag.textContent || '{}');
      render(data);
    } catch (e) {
      console.error('[visjs] parse embedded json error:', e);
      showEmpty('Falha ao interpretar JSON embedado.');
    }
  } else {
    fetch(url, { headers: { 'Accept': 'application/json' } })
      .then(async (r) => { if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`); return r.json(); })
      .then(render)
      .catch((err) => { console.error('[visjs] fetch error:', err); showEmpty(`Falha ao carregar dados: ${String(err).replace(/</g, '&lt;')}`); });
  }
})();