from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Literal

from app.database.connection import get_db
from app.services.graph.engine import (
    fetch_candidate_nodes,
    build_knn_graph,
    a_star_search,
    get_congestion_factor,
    haversine_km,
)
from app.services.graph.multimodal import (
    fetch_nodes_in_bbox,
    fetch_metro_segments,
    build_combined_graph,
    multi_modal_a_star,
    build_steps_from_path,
    estimate_time_minutes,
    get_cab_fare_per_km,
    ROAD_DETOUR_FACTOR,
    SURGE_MULTIPLIER,
    CAB_BASE_SPEED_KMH,
)

router = APIRouter(prefix="/api/routing", tags=["routing"])


@router.get("/route")
async def get_route(
    source_id: int = Query(..., description="Source transit_node id"),
    target_id: int = Query(..., description="Destination transit_node id"),
    node_type: Literal["BMTC_BUS_STOP", "METRO_STATION"] = Query(
        "BMTC_BUS_STOP", description="Kis type ke nodes ke beech route chahiye"
    ),
    time_slot: Literal["MORNING", "AFTERNOON", "EVENING", "NIGHT"] = Query(
        "MORNING", description="Congestion calculation ke liye time slot"
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Multi-objective A* routing engine (Module 2).
    Do transit nodes ke beech best route dhoondta hai, distance + congestion +
    fare + safety ko combine karke.
    """
    # Source aur target node ke coordinates nikalo
    node_query = text("""
        SELECT id, name, ST_Y(location) as lat, ST_X(location) as lon
        FROM transit_nodes WHERE id IN (:source_id, :target_id)
    """)
    result = await db.execute(node_query, {"source_id": source_id, "target_id": target_id})
    endpoints = {row.id: row for row in result.all()}

    if source_id not in endpoints or target_id not in endpoints:
        raise HTTPException(status_code=404, detail="Source ya target node database mein nahi mila")

    src = endpoints[source_id]
    tgt = endpoints[target_id]

    # Bounding box banao (source + target ke around, thoda buffer ke saath)
    buffer_deg = 0.03  # roughly ~3km buffer
    min_lat = min(src.lat, tgt.lat) - buffer_deg
    max_lat = max(src.lat, tgt.lat) + buffer_deg
    min_lon = min(src.lon, tgt.lon) - buffer_deg
    max_lon = max(src.lon, tgt.lon) + buffer_deg

    candidate_nodes = await fetch_candidate_nodes(db, min_lat, max_lat, min_lon, max_lon, node_type)

    if len(candidate_nodes) < 2:
        raise HTTPException(
            status_code=400,
            detail="Is area mein routing ke liye kaafi nodes nahi mile. Bounding box bahut chota hai.",
        )

    nodes_by_id = {n["id"]: dict(n) for n in candidate_nodes}

    if source_id not in nodes_by_id or target_id not in nodes_by_id:
        raise HTTPException(
            status_code=400,
            detail="Source/target node candidate set mein nahi hai (bounding box issue).",
        )

    graph = build_knn_graph(candidate_nodes)
    congestion = await get_congestion_factor(db, time_slot)

    route = a_star_search(graph, nodes_by_id, source_id, target_id, congestion)

    if route is None:
        raise HTTPException(
            status_code=404,
            detail="In do points ke beech koi connected path nahi mila (shayad bahut door hain ya isolated hain).",
        )

    return {
        "source": {"id": src.id, "name": src.name},
        "target": {"id": tgt.id, "name": tgt.name},
        "time_slot": time_slot,
        "candidate_nodes_considered": len(candidate_nodes),
        "route": route,
    }


@router.get("/plan-trip")
async def plan_trip(
    source_lat: float = Query(...),
    source_lon: float = Query(...),
    dest_lat: float = Query(...),
    dest_lon: float = Query(...),
    time_slot: Literal["MORNING", "AFTERNOON", "EVENING", "NIGHT"] = Query("MORNING"),
    db: AsyncSession = Depends(get_db),
):
    """
    Google-Maps-style trip planner — kisi bhi 2 arbitrary points ke beech
    ek multi-modal journey (walk + bus + metro) plan karta hai, aur ek
    cab alternative bhi dikhata hai comparison ke liye.
    """
    source = {"lat": source_lat, "lon": source_lon, "name": "Aapki Location"}
    dest = {"lat": dest_lat, "lon": dest_lon, "name": "Destination"}

    buffer_deg = 0.025
    min_lat = min(source_lat, dest_lat) - buffer_deg
    max_lat = max(source_lat, dest_lat) + buffer_deg
    min_lon = min(source_lon, dest_lon) - buffer_deg
    max_lon = max(source_lon, dest_lon) + buffer_deg

    all_nodes = await fetch_nodes_in_bbox(db, min_lat, max_lat, min_lon, max_lon)
    bus_nodes = [dict(n) for n in all_nodes if n["type"] == "BMTC_BUS_STOP"]
    metro_nodes = [dict(n) for n in all_nodes if n["type"] == "METRO_STATION"]

    metro_segments = await fetch_metro_segments(db, [n["id"] for n in metro_nodes])
    congestion = await get_congestion_factor(db, time_slot)

    transit_option = None
    if bus_nodes or metro_nodes:
        graph, nodes_by_id = build_combined_graph(bus_nodes, metro_nodes, metro_segments, source, dest)
        path_edges = multi_modal_a_star(graph, nodes_by_id, congestion)

        if path_edges:
            steps = build_steps_from_path(path_edges, nodes_by_id)
            total_distance = sum(s["distance_km"] for s in steps)
            total_fare = 0.0
            total_time = 0.0
            for s in steps:
                total_time += estimate_time_minutes(s["distance_km"], s["mode"], congestion)
                if s["mode"] == "BUS":
                    total_fare += s["distance_km"] * 2.0
                elif s["mode"] == "METRO":
                    total_fare += 10.0 + s["distance_km"] * 2.0

            transit_option = {
                "steps": steps,
                "total_distance_km": round(total_distance, 2),
                "total_fare": round(total_fare, 2),
                "total_time_min": round(total_time, 1),
                "congestion_factor": round(congestion, 2),
            }

    # --- Cab option (hamesha calculate karo, comparison ke liye) ---
    straight_line_km = haversine_km(source_lat, source_lon, dest_lat, dest_lon)
    cab_distance_km = round(straight_line_km * ROAD_DETOUR_FACTOR, 2)
    cab_fare_per_km = await get_cab_fare_per_km(db)
    cab_fare = round(cab_distance_km * cab_fare_per_km * (1 + SURGE_MULTIPLIER * congestion), 2)
    cab_speed = CAB_BASE_SPEED_KMH * (1 - 0.4 * congestion)
    cab_time_min = round((cab_distance_km / cab_speed) * 60, 1)

    cab_option = {
        "distance_km": cab_distance_km,
        "fare": cab_fare,
        "time_min": cab_time_min,
        "congestion_factor": round(congestion, 2),
    }

    if transit_option is None:
        raise HTTPException(
            status_code=404,
            detail="Is area mein transit option nahi mila. Sirf cab option available hai.",
        )

    return {
        "time_slot": time_slot,
        "transit_option": transit_option,
        "cab_option": cab_option,
    }