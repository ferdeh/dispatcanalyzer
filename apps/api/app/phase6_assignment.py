from __future__ import annotations

from collections.abc import Iterable

import networkx as nx


def optimize_global_assignment(
    shipment_ids: Iterable[str],
    vehicle_ids: Iterable[str],
    compatible_scores: dict[tuple[str, str], float],
) -> dict[str, tuple[str, float]]:
    """Maximum-weight one-to-one shipment/vehicle assignment.

    This is an exact maximum-weight bipartite matching, not a per-shipment greedy
    choice. Sorting node/edge insertion keeps tie behavior deterministic.
    """
    shipments = sorted(set(shipment_ids))
    vehicles = sorted(set(vehicle_ids))
    if not shipments or not vehicles or not compatible_scores:
        return {}
    graph = nx.Graph()
    shipment_nodes = {shipment_id: ("shipment", shipment_id) for shipment_id in shipments}
    vehicle_nodes = {vehicle_id: ("vehicle", vehicle_id) for vehicle_id in vehicles}
    graph.add_nodes_from(shipment_nodes.values(), bipartite=0)
    graph.add_nodes_from(vehicle_nodes.values(), bipartite=1)
    for (shipment_id, vehicle_id), score in sorted(compatible_scores.items()):
        if shipment_id in shipment_nodes and vehicle_id in vehicle_nodes and float(score) > 0:
            graph.add_edge(shipment_nodes[shipment_id], vehicle_nodes[vehicle_id], weight=float(score))
    matching = nx.algorithms.matching.max_weight_matching(graph, maxcardinality=False, weight="weight")
    result: dict[str, tuple[str, float]] = {}
    for left, right in matching:
        shipment_node, vehicle_node = (left, right) if left[0] == "shipment" else (right, left)
        shipment_id, vehicle_id = shipment_node[1], vehicle_node[1]
        result[shipment_id] = (vehicle_id, float(compatible_scores[(shipment_id, vehicle_id)]))
    return result
