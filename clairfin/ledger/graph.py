"""ClaimLedger: the per-run evidence graph (nodes, support/contradiction edges, custody log, authority docket) with JSON persistence for results/<run_id>/ledger.json."""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from clairfin.schemas.aea import AuthorityDecision
from clairfin.schemas.ledger import (
    ChartRegion,
    ConstraintCheck,
    DerivedMetric,
    EdgeRelation,
    LedgerNode,
    TableCell,
    TextSpan,
    node_from_dict,
)
from clairfin.tools.custody import CustodyEvent


class ClaimLedger:
    def __init__(self) -> None:
        self._graph = nx.MultiDiGraph()
        self._custody_log: list[CustodyEvent] = []
        self._authority_docket: list[AuthorityDecision] = []

    # -- chain-of-custody log (clairfin/tools/custody.py) ---------------------

    def log_custody(self, event: CustodyEvent) -> None:
        self._custody_log.append(event)

    def custody_log_for(self, claim_id: str) -> list[CustodyEvent]:
        return [event for event in self._custody_log if event.claim_id == claim_id]

    # -- authority docket (clairfin/tools/authority_docket.py) -----------------

    def log_authority_decision(self, decision: AuthorityDecision) -> None:
        self._authority_docket.append(decision)

    @property
    def authority_docket(self) -> list[AuthorityDecision]:
        return list(self._authority_docket)

    # -- nodes ---------------------------------------------------------------

    def add_node(self, node: LedgerNode) -> None:
        self._graph.add_node(node.node_id, data=node)

    def get_node(self, node_id: str) -> LedgerNode:
        return self._graph.nodes[node_id]["data"]

    def nodes_of_type(self, node_type: str) -> list[LedgerNode]:
        return [d["data"] for _, d in self._graph.nodes(data=True) if d["data"].node_type == node_type]

    # -- edges -----------------------------------------------------------------

    def add_edge(self, source_id: str, target_id: str, relation: EdgeRelation, **attrs: object) -> None:
        self._graph.add_edge(source_id, target_id, relation=relation, **attrs)

    def edges_of_type(self, relation: EdgeRelation) -> list[tuple[str, str]]:
        return [(u, v) for u, v, d in self._graph.edges(data=True) if d.get("relation") == relation]

    def edge_score(self, source_id: str, target_id: str) -> float:
        """First edge's `score` attribute between two nodes, or 0.0 if there's no edge/score."""
        edge_data = self._graph.get_edge_data(source_id, target_id)
        if not edge_data:
            return 0.0
        first_edge = next(iter(edge_data.values()))
        return float(first_edge.get("score", 0.0))

    def support_subgraph(self, claim_id: str) -> list[str]:
        """Node IDs of everything that `SUPPORTS` this claim."""
        return [u for u, _, d in self._graph.in_edges(claim_id, data=True) if d.get("relation") == "SUPPORTS"]

    def contradiction_subgraph(self, claim_id: str) -> list[str]:
        """Node IDs of everything that `CONTRADICTS` this claim."""
        return [u for u, _, d in self._graph.in_edges(claim_id, data=True) if d.get("relation") == "CONTRADICTS"]

    # -- human-readable rendering, shared by every agent that quotes evidence --

    def describe(self, node_id: str) -> str:
        """One citable line for a node: `[source, p.N] <content>`, format depending on node type."""
        node = self.get_node(node_id)
        source = getattr(node, "source", None)
        page = getattr(node, "page", None)
        prefix = f"[{source}, p.{page}] " if source is not None else ""

        if isinstance(node, TextSpan):
            tag = f"({node.hedge_type}) " if node.hedge_type and node.hedge_type != "fact" else ""
            body = f"{tag}{node.text}"
        elif isinstance(node, TableCell):
            unit = f" {node.unit}" if node.unit else ""
            body = f"{node.row_label} / {node.col_label} = {node.value}{unit}"
        elif isinstance(node, ChartRegion):
            body = node.description
        elif isinstance(node, DerivedMetric):
            body = f"{node.formula} = {node.value}"
        elif isinstance(node, ConstraintCheck):
            body = node.detail
        else:
            body = getattr(node, "text", str(node))

        return f"{prefix}{body}"

    # -- persistence (docs/implementation_mapping.md §3: results/<run_id>/ledger.json) ----------

    def to_json(self) -> str:
        nodes = [d["data"].model_dump() for _, d in self._graph.nodes(data=True)]
        edges = [
            {"source": u, "target": v, **{k: v2 for k, v2 in d.items()}}
            for u, v, d in self._graph.edges(data=True)
        ]
        custody_log = [event.model_dump() for event in self._custody_log]
        authority_docket = [decision.model_dump() for decision in self._authority_docket]
        return json.dumps(
            {"nodes": nodes, "edges": edges, "custody_log": custody_log, "authority_docket": authority_docket},
            indent=2,
        )

    @classmethod
    def from_json(cls, raw: str) -> "ClaimLedger":
        payload = json.loads(raw)
        ledger = cls()
        for node_data in payload["nodes"]:
            ledger.add_node(node_from_dict(node_data))
        for edge in payload["edges"]:
            source, target = edge.pop("source"), edge.pop("target")
            relation = edge.pop("relation")
            ledger.add_edge(source, target, relation, **edge)
        for event_data in payload.get("custody_log", []):
            ledger.log_custody(CustodyEvent.model_validate(event_data))
        for decision_data in payload.get("authority_docket", []):
            decision_data.pop("asymmetry_changed_outcome", None)  # computed field, not an init arg
            ledger.log_authority_decision(AuthorityDecision.model_validate(decision_data))
        return ledger

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "ClaimLedger":
        return cls.from_json(path.read_text(encoding="utf-8"))
