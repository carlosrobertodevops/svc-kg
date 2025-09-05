// =============================================================================
// Arquivo: static/vis-embed.js
// Versão: v1.7.20
// Objetivo: Script de incorporação do grafo (vis-network) para o endpoint /visjs
// Funções/métodos:
// - boot(): inicializa rede lendo dados (embedado ou da API) e monta o grafo
// - transformRawToVis(raw): saneia o payload (IDs únicos por tipo + mapeia cores)
// - buildOptions(): opções visuais (arestas finas, física, interação)
// - wireUi(network, nodesDs, edgesDs): busca, imprimir, recarregar e destaque
// - highlightAndPull(network, nodesDs, nodeId): destaca e "puxa" o nó encontrado
// =============================================================================

(() => {
  'use strict';

  const COLORS = {
    cv: '#e53935',        // CV = vermelho
    pcc: '#1e88e5',       // PCC = azul
    funcao: '#fdd835',    // função = amarelo
    memberDefault: '#90a4ae',
    factionDefault: '#9e9e9e',
    edge: '#bdbdbd',
    edgeHighlight: '#424242'
  };

  function log(...args) {
    try {
      const badge = document.getElementById('badge');
      const debug = (document.getElementById('mynetwork')?.dataset?.debug || 'false') === 'true';
      if (badge) badge.style.display = debug ? 'inline-block' : 'none';
      if (debug) console.debug('[vis-embed]', ...args);
    } catch (_) {}
  }

  function readEmbeddedData() {
    const script = document.getElementById('__KG_DATA__');
    if (!script) return null;
    try {
      return JSON.parse(script.textContent || '{}');
    } catch (e) {
      console.error('Falha ao parsear __KG_DATA__:', e);
      return null;
    }
  }

  async function fetchFromApi(endpoint) {
    const url = endpoint || '/v1/graph/membros?include_co=true';
    const res = await fetch(url, { headers: { 'accept': 'application/json' } });
    if (!res.ok) throw new Error(`Falha ao buscar dados (${res.status})`);
    return res.json();
  }

  function transformRawToVis(raw) {
    // --- 1) Prefixos para IDs únicos por tipo (elimina colisões como "6")
    const key = {
      membro: (id) => `m:${id}`,
      faccao: (id) => `f:${id}`,
      funcao: (id) => `r:${id}`
    };

    // Mapas auxiliares
    const factionsById = new Map();  // id -> {label, colorHex}
    const memberToFaction = new Map(); // membroIdRaw -> faccaoIdRaw (via PERTENCE_A)

    // --- 2) Registrar facções + cores
    for (const n of raw.nodes || []) {
      if (n.type === 'faccao') {
        const label = (n.label || '').trim();
        const up = label.toUpperCase();
        let color = COLORS.factionDefault;
        if (up === 'CV') color = COLORS.cv;
        else if (up.includes('PCC')) color = COLORS.pcc;
        factionsById.set(String(n.id), { label, color });
      }
    }

    // --- 3) Descobrir a facção de cada membro via arestas PERTENCE_A
    for (const e of raw.edges || []) {
      if (e.relation === 'PERTENCE_A') {
        // source: membro.id | target: faccao.id (ids crús do backend)
        memberToFaction.set(String(e.source), String(e.target));
      }
    }

    // --- 4) Montar nós Vis (com IDs únicos)
    const nodesMap = new Map(); // idVis -> node
    for (const n of raw.nodes || []) {
      const base = {
        id: null,
        label: (n.label || '').trim() || `${n.type} ${n.id}`,
        value: n.size ? Math.max(6, Math.min(40, Math.round(n.size / 3))) : undefined, // escala suave
        // guardamos metadados úteis
        raw_id: String(n.id),
        kind: n.type // 'membro' | 'faccao' | 'funcao'
      };

      if (n.type === 'membro') {
        base.id = key.membro(n.id);
        // cor herdada da facção (quando houver)
        const facId = memberToFaction.get(String(n.id));
        const fac = facId ? factionsById.get(facId) : null;
        base.color = { background: fac ? fac.color : COLORS.memberDefault, border: '#263238' };
        base.shape = 'dot';
      } else if (n.type === 'faccao') {
        base.id = key.faccao(n.id);
        const meta = factionsById.get(String(n.id));
        const color = meta?.color || COLORS.factionDefault;
        base.color = { background: color, border: '#263238' };
        base.shape = 'diamond';
        base.font = { bold: { color: '#212121' } };
        base.value = base.value ? base.value + 6 : 18;
      } else if (n.type === 'funcao') {
        base.id = key.funcao(n.id);
        base.color = { background: COLORS.funcao, border: '#5d4037' };
        base.shape = 'hexagon';
        base.value = base.value ? base.value : 14;
      } else {
        // fallback
        base.id = `${n.type || 'n'}:${n.id}`;
        base.color = { background: COLORS.memberDefault, border: '#263238' };
        base.shape = 'dot';
      }

      if (!nodesMap.has(base.id)) nodesMap.set(base.id, base);
      // se houver colisão no mesmo tipo, mantém o primeiro (dados sujos não quebram a rede)
    }

    // --- 5) Montar arestas Vis
    const edges = [];
    for (const e of raw.edges || []) {
      let from, to;
      if (e.relation === 'PERTENCE_A') {
        from = key.membro(e.source);
        to   = key.faccao(e.target);
      } else if (e.relation === 'EXERCE') {
        from = key.membro(e.source);
        to   = key.funcao(e.target);
      } else if (e.relation === 'CO_FACCAO') {
        from = key.membro(e.source);
        to   = key.membro(e.target);
      } else {
        // relação desconhecida -> tentar membro->membro
        from = key.membro(e.source);
        to   = key.membro(e.target);
      }

      edges.push({
        id: `${from}~${to}~${e.relation || ''}`,
        from, to,
        title: e.relation || '',
        width: Math.max(1, Math.min(2, (e.weight || 1) * 0.6)), // arestas finas
        color: { color: COLORS.edge, highlight: COLORS.edgeHighlight, opacity: 0.55 },
        smooth: { enabled: false }
      });
    }

    return { nodes: Array.from(nodesMap.values()), edges };
  }

  function buildOptions() {
    return {
      autoResize: true,
      nodes: {
        shape: 'dot',
        borderWidth: 1,
        shadow: false,
        font: {
          size: 12,
          color: '#212121',
          face: 'Inter, system-ui, -apple-system, "Segoe UI", Roboto, Arial'
        }
      },
      edges: {
        width: 1,
        selectionWidth: 0,
        hoverWidth: 0,
        smooth: false,
        color: { color: COLORS.edge, highlight: COLORS.edgeHighlight, opacity: 0.55 }
      },
      interaction: {
        hover: true,
        tooltipDelay: 200,
        zoomView: true,
        dragView: true,
        dragNodes: true, // você pode “puxar” um nó
        navigationButtons: false,
        keyboard: { enabled: false }
      },
      physics: {
        enabled: true,                      // liga só para estabilizar…
        solver: 'forceAtlas2Based',
        timestep: 0.35,
        minVelocity: 0.5,
        stabilization: { iterations: 150, updateInterval: 10 } // …e depois desliga
      }
    };
  }

  function wireUi(network, nodesDs, edgesDs) {
    // Após estabilizar, desligar física -> arrastar 1 nó não move os demais
    network.once('stabilizationIterationsDone', () => {
      log('stabilizado -> physics OFF');
      network.setOptions({ physics: { enabled: false } });
    });

    // Botões
    document.getElementById('btn-print')?.addEventListener('click', () => window.print());
    document.getElementById('btn-reload')?.addEventListener('click', () => window.location.reload());

    // Busca
    const search = document.getElementById('kg-search');
    let lastHighlighted = null;

    function clearHighlight() {
      if (!lastHighlighted) return;
      const current = nodesDs.get(lastHighlighted);
      if (current) {
        nodesDs.update({
          id: current.id,
          borderWidth: 1,
          shadow: false
        });
      }
      lastHighlighted = null;
    }

    async function doSearch() {
      clearHighlight();
      const q = (search.value || '').trim().toLowerCase();
      if (!q) return;

      // match por label contém ou por raw_id exato
      const all = nodesDs.get();
      let match = all.find(n => (n.label || '').toLowerCase().includes(q));
      if (!match) match = all.find(n => (n.raw_id || '').toLowerCase() === q);

      if (!match) {
        alert('Nenhum nó encontrado.');
        return;
      }

      highlightAndPull(network, nodesDs, match.id);
      lastHighlighted = match.id;
    }

    search?.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') doSearch();
    });
  }

  function highlightAndPull(network, nodesDs, nodeId) {
    // Destaque visual
    const node = nodesDs.get(nodeId);
    if (!node) return;

    nodesDs.update({
      id: nodeId,
      borderWidth: 3,
      shadow: { enabled: true, size: 18, x: 2, y: 2 }
    });

    // "Puxar" o nó: move um pouco para fora do centro e foca a câmera
    const pos = network.getPositions([nodeId])[nodeId] || { x: 0, y: 0 };
    const pulled = { id: nodeId, x: pos.x + 160, y: pos.y - 60, fixed: { x: false, y: false } };
    nodesDs.update(pulled);

    network.focus(nodeId, { scale: 1.2, animation: { duration: 500, easingFunction: 'easeInOutQuad' } });
  }

  async function boot() {
    const container = document.getElementById('mynetwork');
    if (!container) return;

    let raw = null;
    const source = container.dataset.source || 'server';
    const endpoint = container.dataset.endpoint;

    try {
      raw = source === 'server' ? readEmbeddedData() : null;
      if (!raw) raw = await fetchFromApi(endpoint);
    } catch (e) {
      console.error('Erro ao obter dados do grafo:', e);
      container.innerHTML = `<pre style="color:#b71c1c">Erro ao carregar dados do grafo: ${String(e?.message || e)}</pre>`;
      return;
    }

    const visData = transformRawToVis(raw);
    log('visData', visData);

    const nodesDs = new vis.DataSet(visData.nodes);
    const edgesDs = new vis.DataSet(visData.edges);

    const options = buildOptions();
    const network = new vis.Network(container, { nodes: nodesDs, edges: edgesDs }, options);

    wireUi(network, nodesDs, edgesDs);
  }

  // init
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
