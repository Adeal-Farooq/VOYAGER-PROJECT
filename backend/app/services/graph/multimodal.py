"""
Travel OS - Multi-Modal Trip Planner (Google-Maps-style)

Yeh engine kisi bhi 2 arbitrary points (lat/lon) ke beech ek complete journey
plan karta hai — jisme WALK, BUS, aur METRO teeno modes combine ho sakte hain
(jaise Google Maps karta hai: "walk to stop -> take bus -> walk to destination").
Saath hi ek CAB alternative bhi calculate karta hai comparison ke liye.
"""

import heapq
import math
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.graph.engine import haversine_km, get_congestion_factor

WALK_RADIUS_KM = 1.2        # source/dest se kitni door tak walk karke stop dhundna hai
TRANSFER_RADIUS_KM = 0.5    # bus-metro transfer ke liye max walk distance
MAX_BUS_EDGE_KM = 2.0
MAX_BUS_NEIGHBORS = 6

WALK_SPEED_KMH = 4.5
BUS_SPEED_KMH = 18.0
METRO_SPEED_KMH = 32.0
CAB_BASE_SPEED_KMH = 28.0

BUS_FARE_PER_KM = 2.0
METRO_BASE_FARE = 10.0
METRO_FARE_PER_KM = 2.0
SURGE_MULTIPLIER = 0.6
DEFAULT_CAB_FARE_PER_KM = 15.0  # fallback agar data se nikal na paye
ROAD_DETOUR_FACTOR = 1.3        # straight-line distance ko real road distance ke kareeb lane ke liye


async def get_cab_fare_per_km(session: AsyncSession) -> float:
    """Real ride_bookings data se average cab fare/km nikalta hai"""
    query = text("""
        SELECT AVG(total_fare / distance_km) as avg_rate
        FROM ride_bookings
        WHERE booking_status = 'Success'
          AND distance_km BETWEEN 1 AND 40
          AND total_fare > 0
    """)
    result = await session.execute(query)
    rate = result.scalar()
    return float(rate) if rate else DEFAULT_CAB_FARE_PER_KM


async def fetch_nodes_in_bbox(session: AsyncSession, min_lat, max_lat, min_lon, max_lon):
    """BMTC aur Metro dono types ke nodes ek saath fetch karta hai (bus stops ke saath route metadata bhi)"""
    query = text("""
        SELECT id, name, type, ST_Y(location) as lat, ST_X(location) as lon, metadata_json
        FROM transit_nodes
        WHERE ST_Y(location) BETWEEN :min_lat AND :max_lat
          AND ST_X(location) BETWEEN :min_lon AND :max_lon
    """)
    result = await session.execute(
        query, {"min_lat": min_lat, "max_lat": max_lat, "min_lon": min_lon, "max_lon": max_lon}
    )
    return result.mappings().all()


async def fetch_metro_segments(session: AsyncSession, metro_node_ids: list[int]):
    """Real metro route_segments (line ke sequence wale connections) fetch karta hai"""
    if not metro_node_ids:
        return []
    query = text("""
        SELECT source_node_id, target_node_id, spatial_distance
        FROM route_segments
        WHERE transit_type = 'METRO'
          AND source_node_id = ANY(:ids)
          AND target_node_id = ANY(:ids)
    """)
    result = await session.execute(query, {"ids": metro_node_ids})
    return result.all()


def build_combined_graph(
    bus_nodes: list[dict],
    metro_nodes: list[dict],
    metro_segments: list,
    source: dict,
    dest: dict,
) -> tuple[dict[str, list[tuple[str, float, str]]], dict[str, dict]]:
    """
    Ek combined graph banata hai jisme teen tarah ke edges hain:
    - WALK edges (source->nearby stops, stops->dest, bus<->metro transfers)
    - BUS edges (proximity-based, jaise pehle wale engine mein)
    - METRO edges (real route_segments se, sequential stations)

    Returns: (graph {node_id: [(neighbor_id, distance_km, mode)]}, nodes_by_id)
    """
    nodes_by_id: dict[str, dict] = {"SRC": {**source, "mode_type": "VIRTUAL"}, "DST": {**dest, "mode_type": "VIRTUAL"}}
    graph: dict[str, list[tuple[str, float, str]]] = {"SRC": [], "DST": []}

    for n in bus_nodes:
        key = f"BUS_{n['id']}"
        nodes_by_id[key] = {**dict(n), "mode_type": "BUS"}
        graph[key] = []

    for n in metro_nodes:
        key = f"METRO_{n['id']}"
        nodes_by_id[key] = {**dict(n), "mode_type": "METRO"}
        graph[key] = []

    def add_edge(a, b, dist_km, mode):
        graph[a].append((b, dist_km, mode))
        graph[b].append((a, dist_km, mode))

    # --- Metro edges: real sequential connections ---
    metro_id_to_key = {n["id"]: f"METRO_{n['id']}" for n in metro_nodes}
    for seg in metro_segments:
        a = metro_id_to_key.get(seg.source_node_id)
        b = metro_id_to_key.get(seg.target_node_id)
        if a and b:
            add_edge(a, b, (seg.spatial_distance or 0) / 1000.0, "METRO")

    # --- Bus edges: proximity KNN graph ---
    for i, na in enumerate(bus_nodes):
        distances = []
        for j, nb in enumerate(bus_nodes):
            if i == j:
                continue
            d = haversine_km(na["lat"], na["lon"], nb["lat"], nb["lon"])
            if d <= MAX_BUS_EDGE_KM:
                distances.append((nb["id"], d))
        distances.sort(key=lambda x: x[1])
        for neighbor_id, d in distances[:MAX_BUS_NEIGHBORS]:
            add_edge(f"BUS_{na['id']}", f"BUS_{neighbor_id}", d, "BUS")

    # --- Transfer edges: bus <-> metro within walking distance ---
    for bn in bus_nodes:
        for mn in metro_nodes:
            d = haversine_km(bn["lat"], bn["lon"], mn["lat"], mn["lon"])
            if d <= TRANSFER_RADIUS_KM:
                add_edge(f"BUS_{bn['id']}", f"METRO_{mn['id']}", d, "WALK")

    # --- Virtual SRC/DST walk edges to nearby stops ---
    all_stop_nodes = [(f"BUS_{n['id']}", n) for n in bus_nodes] + [(f"METRO_{n['id']}", n) for n in metro_nodes]
    for key, n in all_stop_nodes:
        d_src = haversine_km(source["lat"], source["lon"], n["lat"], n["lon"])
        if d_src <= WALK_RADIUS_KM:
            add_edge("SRC", key, d_src, "WALK")
        d_dst = haversine_km(dest["lat"], dest["lon"], n["lat"], n["lon"])
        if d_dst <= WALK_RADIUS_KM:
            add_edge("DST", key, d_dst, "WALK")

    # --- Fallback: direct walk SRC->DST (agar koi transit path na mile) ---
    direct_d = haversine_km(source["lat"], source["lon"], dest["lat"], dest["lon"])
    add_edge("SRC", "DST", direct_d, "WALK")

    return graph, nodes_by_id


def mode_edge_cost(distance_km: float, mode: str, congestion: float) -> float:
    """Har mode ka apna cost formula — walk sirf distance, bus/metro mein fare+congestion bhi"""
    distance_m = distance_km * 1000
    if mode == "WALK":
        return distance_m  # sirf distance, koi fare/congestion penalty nahi
    elif mode == "BUS":
        fare = distance_km * BUS_FARE_PER_KM
        return distance_m + 500 * congestion + 20 * fare
    elif mode == "METRO":
        fare = METRO_BASE_FARE + distance_km * METRO_FARE_PER_KM
        return distance_m + 300 * congestion + 15 * fare  # metro thoda kam congestion-affected
    return distance_m


def multi_modal_a_star(
    graph: dict[str, list[tuple[str, float, str]]],
    nodes_by_id: dict[str, dict],
    congestion: float,
) -> Optional[list[tuple[str, str, float, str]]]:
    """
    A* search jo SRC se DST tak best multi-modal path dhoondta hai.
    Returns: list of (from_id, to_id, distance_km, mode) edges in path order.
    """
    dst = nodes_by_id["DST"]

    def heuristic(node_id: str) -> float:
        n = nodes_by_id[node_id]
        d_km = haversine_km(n["lat"], n["lon"], dst["lat"], dst["lon"])
        return d_km * 1000  # optimistic (walk-only) heuristic — admissible

    open_set = [(heuristic("SRC"), "SRC")]
    came_from: dict[str, tuple[str, float, str]] = {}
    g_score = {"SRC": 0.0}
    visited = set()

    while open_set:
        _, current = heapq.heappop(open_set)

        if current == "DST":
            edges = []
            node = current
            while node in came_from:
                prev, dist_km, mode = came_from[node]
                edges.append((prev, node, dist_km, mode))
                node = prev
            edges.reverse()
            return edges

        if current in visited:
            continue
        visited.add(current)

        for neighbor_id, distance_km, mode in graph.get(current, []):
            if neighbor_id in visited:
                continue
            tentative_g = g_score[current] + mode_edge_cost(distance_km, mode, congestion)
            if neighbor_id not in g_score or tentative_g < g_score[neighbor_id]:
                came_from[neighbor_id] = (current, distance_km, mode)
                g_score[neighbor_id] = tentative_g
                heapq.heappush(open_set, (tentative_g + heuristic(neighbor_id), neighbor_id))

    return None


def find_common_route(node_ids: list[str], nodes_by_id: dict[str, dict]) -> Optional[str]:
    """
    Segment mein aane wale saare stops ke 'routes' metadata ka intersection nikalta hai —
    agar ek hi bus route number saare stops se guzarta hai, to woh return karta hai (real data se).
    """
    route_sets = []
    for nid in node_ids:
        meta = nodes_by_id[nid].get("metadata_json") or {}
        routes = meta.get("routes") or []
        if routes:
            route_sets.append(set(routes))

    if not route_sets:
        return None

    common = set.intersection(*route_sets)
    return sorted(common)[0] if common else None


def build_steps_from_path(edges: list[tuple[str, str, float, str]], nodes_by_id: dict[str, dict]) -> list[dict]:
    """Raw edges ko human-readable steps mein group karta hai (jaise Google Maps directions)"""
    if not edges:
        return []

    steps = []
    current_mode = edges[0][3]
    segment_start = edges[0][0]
    segment_distance = 0.0
    segment_node_ids = [edges[0][0]]
    segment_coords = [
        [nodes_by_id[edges[0][0]]["lon"], nodes_by_id[edges[0][0]]["lat"]]
    ]

    def flush_segment(end_node):
        nonlocal segment_distance, segment_coords
        start_name = nodes_by_id[segment_start].get("name", "Start")
        end_name = nodes_by_id[end_node].get("name", "Destination")

        if current_mode == "WALK":
            meters = round(segment_distance * 1000)
            label = f"Walk {meters}m to {end_name}"
        elif current_mode == "BUS":
            fare = round(segment_distance * BUS_FARE_PER_KM, 2)
            route_number = find_common_route(segment_node_ids + [end_node], nodes_by_id)
            if route_number:
                label = f"Take Bus {route_number} from {start_name} to {end_name} (₹{fare})"
            else:
                label = f"Take Bus from {start_name} to {end_name} (₹{fare}) — exact route number match nahi mila"
        elif current_mode == "METRO":
            fare = round(METRO_BASE_FARE + segment_distance * METRO_FARE_PER_KM, 2)
            label = f"Take Metro from {start_name} to {end_name} (₹{fare})"
        else:
            label = f"Travel from {start_name} to {end_name}"

        steps.append({
            "mode": current_mode,
            "label": label,
            "distance_km": round(segment_distance, 2),
            "coordinates": segment_coords,
        })

    for from_id, to_id, dist_km, mode in edges:
        if mode != current_mode:
            flush_segment(from_id)
            current_mode = mode
            segment_start = from_id
            segment_distance = 0.0
            segment_node_ids = [from_id]
            segment_coords = [[nodes_by_id[from_id]["lon"], nodes_by_id[from_id]["lat"]]]

        segment_distance += dist_km
        segment_node_ids.append(to_id)
        segment_coords.append([nodes_by_id[to_id]["lon"], nodes_by_id[to_id]["lat"]])

    flush_segment(edges[-1][1])
    return steps


def estimate_time_minutes(distance_km: float, mode: str, congestion: float) -> float:
    speed = {"WALK": WALK_SPEED_KMH, "BUS": BUS_SPEED_KMH, "METRO": METRO_SPEED_KMH}.get(mode, 20.0)
    slowdown = 1 + (0.5 * congestion if mode == "BUS" else 0.2 * congestion)
    return (distance_km / speed) * 60 * slowdown