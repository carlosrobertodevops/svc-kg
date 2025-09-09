// =============================================================================
// Arquivo: static/vis-embed.js
// Versão: v1.7.21
// Objetivo: Inicializar vis-network lendo JSON embutido; dedup; cores CV/PCC/funções;
//           arestas muito finas; estabiliza e desliga física; busca com destaque;
//           layout/physics tunado + painel "configure" (physics).
// Atualização: 09/09/2025 09h55min
// =============================================================================

(function () {
  const RED = "#D32F2F";     // CV
  const BLUE = "#0D47A1";    // PCC
  const YELLOW = "#FFD700";  // funções
  const GREY = "#607D8B";
  const EDGE_DEFAULT = "#B0BEC5";

  function mapColors(node, edge, labelMap, typeMap) {
    if (node) {
      const label = String(node.label || "").toLowerCase();
      const type = String(node.type || "").toLowerCase();
      // aceita "faccao", "facção" e variações
      if (type.includes("facc")) {
        if (label.includes("cv")) return RED;
        if (label.includes("pcc")) return BLUE;
      }
      if (type.includes("funç") || type === "funcao") return YELLOW;
      if (label.includes("cv")) return RED;
      if (label.includes("pcc")) return BLUE;
      return GREY;
    }
    if (edge) {
      const src = String(edge.from);
      const dst = String(edge.to);
      const rel = String(edge.relation || "");
      const ts = String(typeMap[src] || "").toLowerCase();
      const td = String(typeMap[dst] || "").toLowerCase();
      if (ts.includes("funç") || ts === "funcao" || td.includes("funç") || td === "funcao" ||
          rel === "FUNCAO_DA_FACCAO" || rel === "EXERCE") {
        return YELLOW;
      }
      const ls = String(labelMap[src] || "").toLowerCase();
      const ld = String(labelMap[dst] || "").toLowerCase();
      if (ls.includes("cv") || ld.includes("cv"))   return RED;
      if (ls.includes("pcc") || ld.includes("pcc")) return BLUE;
      return EDGE_DEFAULT;
    }
    return GREY;
  }

  function buildDataFromEmbedded() {
    const el = document.getElementById("__KG_DATA__");
    if (!el) return { nodes: [], edges: [] };
    let data;
    try { data = JSON.parse(el.textContent || "{}"); }
    catch { data = { nodes: [], edges: [] }; }

    // Dedup de nós e normalização
    const nodes = [];
    const seenNodes = new Set();
    (data.nodes || []).forEach(n => {
      const id = String(n.id);
      if (!id || seenNodes.has(id)) return;
      seenNodes.add(id);
      nodes.push({
        id,
        label: String(n.label || id),
        type: String(n.type || "").toLowerCase(),
        size: Number(n.size || 8),
        group: n.group
      });
    });

    // Dedup de arestas (source/target -> from/to)
    const edges = [];
    const seenEdges = new Set();
    (data.edges || []).forEach(e => {
      const from = String(e.from || e.source || "");
      const to = String(e.to || e.target || "");
      if (!from || !to) return;
      const rel = String(e.relation || "");
      const key = `${from}::${to}::${rel}`;
      if (seenEdges.has(key)) return;
      seenEdges.add(key);
      edges.push({
        from, to,
        relation: rel,
        value: Number(e.value || e.weight || 1),
        arrows: e.arrows === "to" ? "to" : ""
      });
    });

    return { nodes, edges };
  }

  function attachSearch(network, nodes) {
    const input = document.getElementById("kg-search");
    if (!input) return;
    function doSearch(term) {
      term = (term || "").trim().toLowerCase();
      if (!term) return;
      const all = nodes.get();
      let hitId = null;
      all.forEach(n => {
        nodes.update({ id: n.id, borderWidth: 0, font: { size: 12 } });
        if (!hitId) {
          const l = String(n.label || "").toLowerCase();
          if (l.includes(term) || String(n.id).toLowerCase().includes(term)) hitId = n.id;
        }
      });
      if (hitId) {
        nodes.update({ id: hitId, borderWidth: 3, font: { size: 14 } });
        network.focus(hitId, { scale: 1.2, animation: { duration: 500 } });
        network.selectNodes([hitId]);
      }
    }
    input.addEventListener("keydown", ev => { if (ev.key === "Enter") doSearch(input.value); });
  }

  window.__KG_INIT_VIS__ = function (containerId, initialSearch) {
    const container = document.getElementById(containerId);
    if (!container) return;

    // Usa somente o JSON incorporado (data-source="server")
    const dataset = buildDataFromEmbedded();

    // índices auxiliares
    const labelMap = {};
    const typeMap = {};
    dataset.nodes.forEach(n => { labelMap[n.id] = n.label || ""; typeMap[n.id] = n.type || ""; });

    // monta DataSet com cores aplicadas
    const nodes = new vis.DataSet(dataset.nodes.map(n => ({
      id: n.id,
      label: n.label,
      size: n.size,
      color: mapColors(n, null, labelMap, typeMap)
    }))));

    const edges = new vis.DataSet(dataset.edges.map(e => ({
      from: e.from,
      to: e.to,
      arrows: e.arrows ? { to: { enabled: true, scaleFactor: 0.2 } } : "",
      color: { color: mapColors(null, e, labelMap, typeMap), opacity: 0.35 },
      width: 0.5,
      smooth: false
    }))));

    const data = { nodes, edges };

    // opções com layout/physics tunado + configure panel
    const options = {
      layout: { improvedLayout: true, randomSeed: 7 },
      nodes: {
        shape: "dot",
        font: { size: 12, strokeWidth: 1, strokeColor: "#ffffff" },
        scaling: { min: 8, max: 40, label: { enabled: true, min: 8, max: 22 } }
      },
      edges: {
        width: 0.5,
        selectionWidth: 0.2,
        smooth: false,
        color: { opacity: 0.35 },
        arrows: { to: { enabled: true, scaleFactor: 0.5 } }
      },
      interaction: {
        hover: true, dragNodes: true, zoomView: true,
        tooltipDelay: 120, hideEdgesOnDrag: true, hideEdgesOnZoom: true
      },
      configure: { enabled: true, filter: "physics", showButton: true },
      physics: {
        enabled: true,
        solver: "forceAtlas2Based",
        stabilization: { iterations: 900, fit: true },
        forceAtlas2Based: {
          gravitationalConstant: -18000,
          centralGravity: 0.02,
          springLength: 220,
          springConstant: 0.015,
          damping: 0.45,
          avoidOverlap: 1.0
        },
        repulsion: { nodeDistance: 240, springLength: 200, springConstant: 0.02, damping: 0.35 }
      }
    };

    const network = new vis.Network(container, data, options);

    network.once("stabilizationIterationsDone", function () {
      network.setOptions({ physics: false });
    });

    const btnFit = document.getElementById("btn-fit");
    if (btnFit) btnFit.onclick = () => network.fit({ animation: true });

    attachSearch(network, nodes);

    if (initialSearch && String(initialSearch).trim()) {
      setTimeout(() => {
        const input = document.getElementById("kg-search");
        if (input) {
          input.value = initialSearch;
          const ev = new KeyboardEvent("keydown", { key: "Enter" });
          input.dispatchEvent(ev);
        }
      }, 400);
    }
  };
})();


// ---------------------------------------------------------------------------
// Versão legacy (comentada) mantida por compatibilidade/histórico.
// ---------------------------------------------------------------------------
// (function () {
//   const RED = "#D32F2F";     // CV
//   const BLUE = "#0D47A1";    // PCC
//   const YELLOW = "#FFD700";  // funções
//   const GREY = "#607D8B";
//   const EDGE_DEFAULT = "#B0BEC5";
//   /* ... conteúdo anterior omitido propositalmente (somente comentários) ... */
// })();

// // =============================================================================
// // Arquivo: static/vis-embed.js
// // Versão: v1.7.21
// // Objetivo: Inicializar vis-network lendo JSON embutido; dedup; cores CV/PCC/funções;
// //           arestas muito finas; estabiliza e desliga física; busca com destaque.
// // =============================================================================

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
//       // aceita "faccao", "facção" e variações
//       if (type.includes("facc")) {
//         if (label.includes("cv")) return RED;
//         if (label.includes("pcc")) return BLUE;
//       }
//       if (type.includes("funç") || type === "funcao") return YELLOW;
//       if (label.includes("cv")) return RED;
//       if (label.includes("pcc")) return BLUE;
//       return GREY;
//     }
//     if (edge) {
//       const src = String(edge.from);
//       const dst = String(edge.to);
//       const rel = String(edge.relation || "");
//       const ts = String(typeMap[src] || "").toLowerCase();
//       const td = String(typeMap[dst] || "").toLowerCase();
//       if (ts.includes("funç") || ts === "funcao" || td.includes("funç") || td === "funcao" ||
//           rel === "FUNCAO_DA_FACCAO" || rel === "EXERCE") {
//         return YELLOW;
//       }
//       const ls = String(labelMap[src] || "").toLowerCase();
//       const ld = String(labelMap[dst] || "").toLowerCase();
//       if (ls.includes("cv") || ld.includes("cv"))   return RED;
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
//     }))));

//     const edges = new vis.DataSet(dataset.edges.map(e => ({
//       from: e.from,
//       to: e.to,
//       arrows: e.arrows ? { to: { enabled: true, scaleFactor: 0.2 } } : "",
//       color: { color: mapColors(null, e, labelMap, typeMap), opacity: 0.35 },
//       width: 0.5,
//       smooth: false
//     }))));

//     const data = { nodes, edges };
//     const options = {
//       nodes: { shape: "dot", font: { size: 12 } },
//       edges: { width: 0.5, selectionWidth: 0.2, smooth: false, color: { opacity: 0.35 } },
//       interaction: { hover: true, dragNodes: true, zoomView: true },
//       physics: { enabled: true, solver: "forceAtlas2Based", stabilization: { iterations: 400, fit: true } }
//     };

//     const network = new vis.Network(container, data, options);

//     network.once("stabilizationIterationsDone", function () {
//       network.setOptions({ physics: false });
//     });

//     const btnFit = document.getElementById("btn-fit");
//     if (btnFit) btnFit.onclick = () => network.fit({ animation: true });

//     attachSearch(network, nodes);

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
