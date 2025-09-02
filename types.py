from pydantic import BaseModel, Field
from typing import Any, Dict, List

class GraphNode(BaseModel):
    id: str
    label: str
    data: Dict[str, Any] = Field(default_factory=dict)

class GraphEdge(BaseModel):
    source: str
    target: str
    label: str
    data: Dict[str, Any] = Field(default_factory=dict)

class GraphPayload(BaseModel):
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    meta: Dict[str, Any] = Field(default_factory=dict)
