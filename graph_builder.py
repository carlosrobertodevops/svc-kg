from typing import Dict, Any, List
from pyvis.network import Network
import tempfile
import os

def normalize_graph_schema(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Garante chaves esperadas por pyVis: nodes: [{id,label,group,title,value}], edges: [{from,to,label,weight}]
    Se seu RPC já retorna nesse formato, apenas retorna.
    """
    nodes = data.get("nodes") or data.get("nodos") or []
    edges = data.get("edges") or data.get("arestas") or []

    # Ajustes mínimos de nomes de campos
    norm_nodes = []
    for n in nodes:
        norm_nodes.append({
            "id": n.get("id") or n.get("node_id"),
            "label": n.get("label") or n.get("nome") or str(n.get("id")),
            "group": n.get("group") or n.get("grupo"),
            "title": n.get("title") or n.get("descricao") or n.get("label"),
            "value": n.get("value") or n.get("peso") or 1,
        })

    norm_edges = []
    for e in edges:
        norm_edges.append({
            "from": e.get("from") or e.get("origem") or e.get("source"),
            "to": e.get("to") or e.get("destino") or e.get("target"),
            "label": e.get("label") or e.get("tipo"),
            "weight": e.get("weight") or e.get("peso") or 1,
        })

    return {"nodes": norm_nodes, "edges": norm_edges}

def truncate_preview(data: Dict[str, Any], max_nodes: int, max_edges: int) -> Dict[str, Any]:
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    # corta nós
    nodes = nodes[:max_nodes]
    allowed_ids = {n["id"] for n in nodes}
    # filtra arestas só com nós existentes e depois limita
    edges = [e for e in edges if e.get("from") in allowed_ids and e.get("to") in allowed_ids]
    edges = edges[:max_edges]
    return {"nodes": nodes, "edges": edges}

def build_pyvis_html(
    data: Dict[str, Any],
    physics: bool = True,
    height: str = "650px",
    width: str = "100%",
) -> str:
    net = Network(height=height, width=width, directed=True)
    net.barnes_hut() if physics else net.toggle_physics(False)

    for n in data.get("nodes", []):
        net.add_node(
            n.get("id"),
            label=n.get("label"),
            title=n.get("title"),
            group=n.get("group"),
            value=n.get("value"),
        )

    for e in data.get("edges", []):
        net.add_edge(
            e.get("from"),
            e.get("to"),
            title=e.get("label"),
            value=e.get("weight"),
        )

    # gera HTML em memória (pyvis >= 0.3.0 tem generate_html)
    if hasattr(net, "generate_html"):
        return net.generate_html(notebook=False)
    # fallback: arquivo temporário
    with tempfile.TemporaryDirectory() as tmpd:
        path = os.path.join(tmpd, "graph.html")
        net.show(path)  # escreve o html
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
