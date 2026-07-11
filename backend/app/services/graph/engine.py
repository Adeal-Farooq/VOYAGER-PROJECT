"""
Travel OS - Multi-Objective Graph Routing Engine (Module 2)

IMPORTANT HONEST NOTE:
Humare paas actual bus-route-sequence data nahi hai (yaani "route X pehle stop A,
phir B, phir C se hoke jaati hai" wala exact order). Isliye yeh engine ek
GEOMETRIC PROXIMITY GRAPH banata hai — nearby stops ko connect karta hai aur
unke beech A* se best path dhoondta hai. Yeh production-grade real-world
routing se thoda simplified hai, lekin PDF spec ke "PostGIS ST_DistanceSphere
fallback" wale approach ke bilkul align mein hai.
"""

import heapq
import math
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ------------------------------------------------------------------
# Cost formula weights: Cost = w1*Distance + w2*Congestion + w3*Fare + w4*(1-Safety)
# ------------------------------------------------------------------
W1_DISTANCE = 1.0
W2_CONGESTION = 500.0   # congestion factor (0-1) scaled to be comparable to meters
W3_FARE = 20.0          # fare (INR) scaled up since fare numbers are small
W4_SAFETY = 500.0       # safety penalty scaled similarly

BUS_FARE_PER_KM = 2.0   # approx BMTC ordinary bus fare rate (INR/km)
SURGE_MULTIPLIER = 0.6  # peak-time surge factor — busy hours cost more (jaise real cabs/autos mein hota hai)
DEFAULT_SAFETY_SCORE = 0.8  # neutral default until Module 3 (crowdsourced safety) is built

MAX_NEIGHBORS = 6        # each node connects to its k nearest neighbors
MAX_EDGE_KM = 2.0         # don't connect nodes farther than this (avoids unrealistic jumps

TIME_SLOT_HOURS = {
    "MORNING": range(6, 10),
    "AFTERNOON": range(10, 16),
    "EVENING": range(16, 20),
    "NIGHT": list(range(20, 24)) + list(range(0, 6)),
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Do lat/long points ke beech real-world distance (km) nikalta hai"""
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


async def get_congestion_factor(session: AsyncSession, time_slot: str) -> float:
    """
    metro_hourly_ridership data ka use karke ek city-wide congestion proxy nikalta hai
    (0.0 = sabse kam bheed, 1.0 = sabse zyada bheed us time_slot mein).
    """
    hours = TIME_SLOT_HOURS.get(time_slot.upper(), TIME_SLOT_HOURS["AFTERNOON"])
    hour_patterns = [f"{h:02d}:00%" for h in hours]

    like_clauses = " OR ".join([f"hour_slot LIKE :h{i}" for i in range(len(hour_patterns))])
    params = {f"h{i}": pattern for i, pattern in enumerate(hour_patterns)}

    query = text(f"""
        SELECT AVG(passenger_count) as avg_count
        FROM metro_hourly_ridership
        WHERE {like_clauses}
    """)
    result = await session.execute(query, params)
    slot_avg = float(result.scalar() or 0.0)

    max_query = text("""
        SELECT MAX(hourly_avg) FROM (
            SELECT AVG(passenger_count) as hourly_avg
            FROM metro_hourly_ridership
            GROUP BY hour_slot
        ) sub
    """)
    max_result = await session.execute(max_query)
    max_avg = float(max_result.scalar() or 1.0)

    if max_avg == 0:
        return 0.0
    return min(slot_avg / max_avg, 1.0)


async def fetch_candidate_nodes(
    session: AsyncSession, min_lat: float, max_lat: float, min_lon: float, max_lon: float, node_type: str
):
    """Bounding box ke andar aane wale saare nodes fetch karta hai (candidate graph nodes)"""
    query = text("""
        SELECT id, name, code, ST_Y(location) as lat, ST_X(location) as lon
        FROM transit_nodes
        WHERE type = :node_type
          AND ST_Y(location) BETWEEN :min_lat AND :max_lat
          AND ST_X(location) BETWEEN :min_lon AND :max_lon
    """)
    result = await session.execute(
        query,
        {
            "node_type": node_type,
            "min_lat": min_lat,
            "max_lat": max_lat,
            "min_lon": min_lon,
            "max_lon": max_lon,
        },
    )
    return result.mappings().all()


def build_knn_graph(nodes: list[dict]) -> dict[int, list[tuple[int, float]]]:
    """
    Har node ko apne k-nearest neighbors se connect karta hai (proximity graph).
    Returns: {node_id: [(neighbor_id, distance_km), ...]}
    """
    graph: dict[int, list[tuple[int, float]]] = {n["id"]: [] for n in nodes}

    for i, node_a in enumerate(nodes):
        distances = []
        for j, node_b in enumerate(nodes):
            if i == j:
                continue
            d = haversine_km(node_a["lat"], node_a["lon"], node_b["lat"], node_b["lon"])
            if d <= MAX_EDGE_KM:
                distances.append((node_b["id"], d))

        distances.sort(key=lambda x: x[1])
        graph[node_a["id"]] = distances[:MAX_NEIGHBORS]

    return graph


def edge_cost(distance_km: float, congestion: float, safety: float = DEFAULT_SAFETY_SCORE) -> float:
    """
    Multi-objective cost formula:
    Cost(u,v) = w1*Distance + w2*Congestion + w3*BaseFare + w4*(1 - Safety)
    """
    distance_m = distance_km * 1000
    fare = distance_km * BUS_FARE_PER_KM
    return (
        W1_DISTANCE * distance_m
        + W2_CONGESTION * congestion
        + W3_FARE * fare
        + W4_SAFETY * (1.0 - safety)
    )


def a_star_search(
    graph: dict[int, list[tuple[int, float]]],
    nodes_by_id: dict[int, dict],
    start_id: int,
    goal_id: int,
    congestion: float,
) -> Optional[dict]:
    """
    A* search jo start se goal tak sabse kam-cost wala path dhoondta hai.
    Heuristic = straight-line (haversine) distance to goal — yeh admissible hai
    kyunki real path kabhi bhi straight-line se chota nahi ho sakta.
    """
    goal_node = nodes_by_id[goal_id]

    def heuristic(node_id: int) -> float:
        n = nodes_by_id[node_id]
        d_km = haversine_km(n["lat"], n["lon"], goal_node["lat"], goal_node["lon"])
        return edge_cost(d_km, congestion)

    open_set = [(heuristic(start_id), start_id)]
    came_from: dict[int, int] = {}
    g_score = {start_id: 0.0}
    visited = set()

    # Backtracking Elimination: track visited-in-current-path to avoid A->B->A loops
    while open_set:
        _, current = heapq.heappop(open_set)

        if current == goal_id:
            # Path reconstruct karo
            path_ids = [current]
            total_distance_km = 0.0
            while current in came_from:
                prev = came_from[current]
                total_distance_km += haversine_km(
                    nodes_by_id[current]["lat"], nodes_by_id[current]["lon"],
                    nodes_by_id[prev]["lat"], nodes_by_id[prev]["lon"],
                )
                current = prev
                path_ids.append(current)
            path_ids.reverse()

            return {
                "path": [nodes_by_id[nid] for nid in path_ids],
                "total_distance_km": round(total_distance_km, 2),
                "base_fare": round(total_distance_km * BUS_FARE_PER_KM, 2),
                "estimated_fare": round(
                    total_distance_km * BUS_FARE_PER_KM * (1 + SURGE_MULTIPLIER * congestion), 2
                ),
                "congestion_factor": round(congestion, 2),
                "total_cost_score": round(g_score[goal_id], 2),
            }

        if current in visited:
            continue
        visited.add(current)

        for neighbor_id, distance_km in graph.get(current, []):
            if neighbor_id in visited:
                continue
            tentative_g = g_score[current] + edge_cost(distance_km, congestion)

            if neighbor_id not in g_score or tentative_g < g_score[neighbor_id]:
                came_from[neighbor_id] = current
                g_score[neighbor_id] = tentative_g
                f_score = tentative_g + heuristic(neighbor_id)
                heapq.heappush(open_set, (f_score, neighbor_id))

    return None  # koi path nahi mila