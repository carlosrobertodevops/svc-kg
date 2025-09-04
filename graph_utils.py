# src/graph_utils.py
from typing import Any, Dict, List, Tuple
from pyvis.network import Network

def normalize_graph(raw: Any) -> Dict[str, List[Dict[str, Any]]]:
    """
    Espera do RPC algo como: {"nodes":[...], "edges":[...]}
    Tenta normalizar casos comuns (ex.: retorno direto do PostgREST como lista).
    """
    if isinstance(raw, dict) and "nodes" in raw and "edges" in raw:
        nodes = raw.get("nodes") or []
        edges = raw.get("edges") or []
        return {"nodes": list(nodes), "edges": list(edges)}

    # Se vier lista simples de registros, tente mapear (fallback muito básico)
    if isinstance(raw, list):
        nodes, edges = [], []
        for item in raw:
            if not isinstance(item, dict):
                continue
            if {"source", "target"} <= item.keys():
                edges.append({
                    "source": item.get("source"),
                    "target": item.get("target"),
                    "label": item.get("label"),
                    "weight": item.get("weight"),
                    "arrows": item.get("arrows", "to" if item.get("directed") else None),
                })
            elif "id" in item or "node_id" in item:
                nid = item.get("id") or item.get("node_id")
                nodes.append({
                    "id": nid,
                    "label": item.get("label", str(nid)),
                    "group": item.get("group"),
                    "title": item.get("title"),
                    "color": item.get("color"),
                })
        return {"nodes": nodes, "edges": edges}

    # Último recurso
    return {"nodes": [], "edges": []}

def truncate_preview(graph: Dict[str, List[Dict[str, Any]]], max_nodes: int, max_edges: int) -> Tuple[Dict[str, Any], bool]:
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    if len(nodes) <= max_nodes and len(edges) <= max_edges:
        return graph, False

    # Corte simples mas consistente: mantenha primeiros edges e os nós que aparecem neles
    edges_trunc = edges[:max_edges]
    keep_ids = set()
    for e in edges_trunc:
        s = e.get("source")
        t = e.get("target")
        if s is not None: keep_ids.add(s)
        if t is not None: keep_ids.add(t)

    nodes_map = {n.get("id"): n for n in nodes}
    kept_nodes = []
    for nid in nodes_map:
        if nid in keep_ids:
            kept_nodes.append(nodes_map[nid])
            if len(kept_nodes) >= max_nodes:
                break

    # Se ainda tiver espaço, complete com nós restantes
    if len(kept_nodes) < max_nodes:
        for n in nodes:
            if n.get("id") not in keep_ids:
                kept_nodes.append(n)
                if len(kept_nodes) >= max_nodes:
                    break

    return {"nodes": kept_nodes, "edges": edges_trunc}, True

def build_pyvis_html(graph: Dict[str, List[Dict[str, Any]]], physics: bool = True, height: str = "100%", width: str = "100%") -> str:
    net = Network(height=height, width=width, bgcolor="#ffffff", font_color="#222", cdn_resources="in_line")
    net.barnes_hut() if physics else net.force_atlas_2based(stop_threshold=0.9)  # ativa física (pyvis sempre aplica um layout)

    # Adiciona nós
    for n in graph.get("nodes", []):
        net.add_node(
            n.get("id"),
            label=n.get("label", str(n.get("id"))),
            title=n.get("title"),
            group=n.get("group"),
            color=n.get("color"),
        )

    # Adiciona arestas
    for e in graph.get("edges", []):
        net.add_edge(
            e.get("source"),
            e.get("target"),
            title=e.get("label"),
            value=e.get("weight"),
            arrows=e.get("arrows") or ""
        )

    # Algumas opções padrão agradáveis no navegador
    net.set_options("""
    var options = {
      interaction: { hover: true, tooltipDelay: 120 },
      nodes: { shape: 'dot', size: 12, scaling: { min: 8, max: 32 } },
      edges: { smooth: { type: 'dynamic' } },
      physics: { enabled: %s }
    }
    """ % ("true" if physics else "false"))

    return net.generate_html()
