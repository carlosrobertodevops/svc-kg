/* global vis */
(function () {
  const el = document.getElementById("mynetwork");
  if (!el) return;

  const THEME = (el.getAttribute("data-theme") || "light").toLowerCase();
  const SOURCE = (el.getAttribute("data-source") || "server").toLowerCase();
  const ENDPOINT = el.getAttribute("data-endpoint") || "/v1/graph/membros";
  const DEBUG = (el.getAttribute("data-debug") || "false") === "true";
  const Q = (el.getAttribute("data-query") || "").trim();

  const CV_COLOR = "#d32f2f";      // vermelho
  const PCC_COLOR = "#0d47a1";     // azul escuro
  const DEFAULT_NODE_BORDER = 1;

  const EDGE_COLORS = {
    "PERTENCE_A": "#9e9e9e",
    "EXERCE": "#00796b",
    "FUNCAO_DA_FACCAO": "#ef6c00",
    "CO_FACCAO": "#8e24aa",
    "CO_FUNCAO": "#546e7a",
  };

  const bg = THEME === "dark" ? "#0b0f19" : "#ffffff";
  document.body.style.backgroundColor = bg;

  function isHttp(u) {
    return /^https?:\/\//i.test(String(u || ""));
  }
  function resolvePhotoPath(v) {
    if (!v) return null;
    const val = String(v).trim();
    if (!val) return null;
    if (isHttp(val)) return val;
    if (val.startsWith("/")) return val;
    if (val.startsWith("assets/")) return "/assets/" + val.slice(7);
    if (val.startsWith("static/")) return "/" + val;
    return "/assets/" + val;
  }

  function hashColor(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) {
      h = (h << 5) - h + str.charCodeAt(i);
      h |= 0;
    }
    const hue = Math.abs(h) % 360;
    return `hsl(${hue},70%,50%)`;
  }

  function faccaoColorByName(nameUpper) {
    if (!nameUpper) return null;
    if (nameUpper.includes("PCC")) return PCC_COLOR;
    if (nameUpper === "CV" || nameUpper.includes("COMANDO VERMELHO")) return CV_COLOR;
    return null;
  }

  function buildVisData(raw) {
    const nodes = [];
    const edges = [];

    // indexa rótulo de nós facção para lookup por id -> nome
    const faccaoLabelById = {};
    (raw.nodes || []).forEach(n => {
      if (!n || n.type !== "faccao") return;
      const id = String(n.id);
      faccaoLabelById[id] = String(n.label || "").trim();
    });

    (raw.nodes || []).forEach(n => {
      if (!n || n.id == null) return;
      const id = String(n.id);
      const label = String(n.label || id);
      const group = String(n.group || n.faccao_id || n.type || "0");
      const photo = resolvePhotoPath(n.photo_url || n.foto_path);

      // cor por facção: procura pelo label da facção associada, senão por group string
      let color = null;
      const facName = faccaoLabelById[group] ? faccaoLabelById[group].toUpperCase() : String(group || "").toUpperCase();
      color = faccaoColorByName(facName) || hashColor(group);

      const node = {
        id,
        label,
        title: label,
        borderWidth: DEFAULT_NODE_BORDER,
        color,
        shape: "circularImage",
        image: photo || "/static/icons/person.svg",
        // guarda original para animações de busca
        _origColor: color
      };
      // mantém type para busca (membro/facção/função)
      if (n.type) node.type = n.type;
      nodes.push(node);
    });

    const validIds = new Set(nodes.map(n => String(n.id)));
    (raw.edges || []).forEach(e => {
      if (!e) return;
      const a = String(e.source);
      const b = String(e.target);
      if (!validIds.has(a) || !validIds.has(b)) return;
      const rel = String(e.relation || "");
      const color = EDGE_COLORS[rel] || "#90a4ae";
      edges.push({
        from: a,
        to: b,
        color,
        width: 1,          // linhas finas
        arrows: "to",
        title: rel ? `${rel}` : undefined
      });
    });

    return { nodes, edges };
  }

  async function getData() {
    if (SOURCE === "server") {
      const block = document.getElementById("__KG_DATA__");
      if (!block) throw new Error("Bloco __KG_DATA__ ausente no modo server.");
      return JSON.parse(block.textContent || "{}");
    } else {
      const url = new URL(ENDPOINT, location.origin);
      // repassa params padrão
      for (const k of ["faccao_id", "include_co", "max_pairs", "max_nodes", "max_edges", "cache"]) {
        const v = (new URLSearchParams(location.search)).get(k);
        if (v != null) url.searchParams.set(k, v);
      }
      if (Q) url.searchParams.set("q", Q);
      const r = await fetch(url.toString(), { credentials: "omit" });
      if (!r.ok) throw new Error(`GET ${url} -> ${r.status}`);
      return await r.json();
    }
  }

  function initUI(network, nodesDS) {
    const q = document.getElementById("kg-search");
    const b = document.getElementById("btn-apply");
    const c = document.getElementById("btn-clear");
    const p = document.getElementById("btn-print");
    const r = document.getElementById("btn-reload");

    function colorObj(orig, opacity) {
      if (typeof orig === "object" && orig) return Object.assign({}, orig, { opacity });
      return {
        background: orig || "#90a4ae",
        border: orig || "#90a4ae",
        highlight: { background: orig || "#90a4ae", border: orig || "#90a4ae" },
        hover: { background: orig || "#90a4ae", border: orig || "#90a4ae" },
        opacity
      };
    }

    function runSearch(txt) {
      const all = nodesDS.get();
      const t = String(txt || "").trim().toLowerCase();

      // reset opacidade
      all.forEach(n => {
        const base = n._origColor || n.color || "#90a4ae";
        nodesDS.update({ id: n.id, color: colorObj(base, 1) });
      });

      if (!t) return;

      const hits = all.filter(n => {
        const lab = String(n.label || "").toLowerCase();
        const typ = String(n.type || "").toLowerCase();
        return lab.includes(t) || typ.includes(t) || String(n.id) === t;
      });

      const hitIds = new Set(hits.map(h => h.id));
      all.forEach(n => {
        if (!hitIds.has(n.id)) {
          const base = n._origColor || n.color || "#90a4ae";
          nodesDS.update({ id: n.id, color: colorObj(base, 0.25) });
        }
      });

      if (hits.length) {
        network.fit({ nodes: hits.map(h => h.id), animation: { duration: 300 } });
      }
    }

    if (p) p.onclick = () => window.print();
    if (r) r.onclick = () => location.reload();
    if (b && q) b.onclick = () => runSearch(q.value);
    if (c && q) c.onclick = () => { q.value = ""; runSearch(""); };
  }

  (async function main() {
    try {
      const raw = await getData();
      const visData = buildVisData(raw);

      const nodes = new vis.DataSet(visData.nodes);
      const edges = new vis.DataSet(visData.edges);

      const container = el;
      const data = { nodes, edges };
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
          stabilization: { enabled: true, iterations: 300 },
          barnesHut: {
            gravitationalConstant: -8000,
            centralGravity: 0.2,
            springLength: 120,
            springConstant: 0.04,
            avoidOverlap: 0.2
          }
        },
        nodes: { borderWidth: 1 },
        edges: { smooth: false, width: 1 }
      };

      const network = new vis.Network(container, data, options);

      // após estabilizar, desliga física => arrastar só o nó, não o grafo inteiro
      network.once("stabilized", () => network.setOptions({ physics: false }));

      // guarda cor original (para busca)
      nodes.get().forEach(n => {
        if (n._origColor == null) {
          nodes.update({ id: n.id, _origColor: n.color });
        }
      });

      initUI(network, nodes);

      if (DEBUG) {
        console.log("KG raw:", raw);
        console.log("visData:", visData);
      }
    } catch (e) {
      console.error(e);
      el.innerHTML = "<pre style='padding:12px'>Falha ao carregar grafo: " + String(e) + "</pre>";
    }
  })();
})();
