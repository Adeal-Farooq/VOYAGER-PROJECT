from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Literal
from datetime import datetime

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

from app.services.graph.direct_bus import find_nearby_gtfs_stops, find_direct_bus_options

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

    # Buffer ko trip ki actual distance ke hisaab se scale karo — chote trip ke liye chota,
    # lambe trip ke liye bada buffer, taaki humesha kaafi candidate nodes milein
    straight_line_km_for_buffer = haversine_km(source_lat, source_lon, dest_lat, dest_lon)
    buffer_deg = max(0.03, (straight_line_km_for_buffer / 111.0) * 0.35)
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

            now = datetime.now()
            current_time_str = now.strftime("%H:%M:%S")

            for s in steps:
                total_time += estimate_time_minutes(s["distance_km"], s["mode"], congestion)
                if s["mode"] == "BUS":
                    # Is specific leg ke liye REAL bus number + schedule dhoondo (GTFS se)
                    leg_start = s["coordinates"][0]   # [lon, lat]
                    leg_end = s["coordinates"][-1]
                    try:
                        src_stops = await find_nearby_gtfs_stops(db, leg_start[1], leg_start[0], radius_km=0.6)
                        dst_stops = await find_nearby_gtfs_stops(db, leg_end[1], leg_end[0], radius_km=0.6)
                        if src_stops and dst_stops:
                            bus_opts = await find_direct_bus_options(
                                db,
                                [x["stop_id"] for x in src_stops],
                                [x["stop_id"] for x in dst_stops],
                                after_time=current_time_str,
                                max_results=1,
                            )
                            if bus_opts:
                                s["bus_details"] = {
                                    "route_number": bus_opts[0]["route_number"],
                                    "departure_time": bus_opts[0]["departure_time"],
                                    "arrival_time": bus_opts[0]["arrival_time"],
                                    "fare": bus_opts[0]["fare"],
                                    "fare_is_estimated": bus_opts[0]["fare_is_estimated"],
                                }
                                # Isi specific bus ka REAL road-shape bhi attach karo
                                # (generic/unrelated shape nahi — sirf yehi jo plan mein use ho raha hai)
                                if bus_opts[0].get("real_road_shape"):
                                    s["coordinates"] = bus_opts[0]["real_road_shape"]
                    except Exception:
                        pass  # real data na mile to generic estimate hi rehne do

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


@router.get("/direct-buses")
async def get_direct_buses(
    source_lat: float = Query(...),
    source_lon: float = Query(...),
    dest_lat: float = Query(...),
    dest_lon: float = Query(...),
    after_time: str = Query("00:00:00", description="HH:MM:SS format — isके baad wali agli buses dikhayega"),
    db: AsyncSession = Depends(get_db),
):
    """
    REAL BMTC GTFS schedule data use karke batata hai — kaunsi bus number(s)
    seedhi (direct) jaati hain, kis scheduled time pe, kitne stops ke saath,
    aur real fare (jahan data available hai).
    """
    source_stops = await find_nearby_gtfs_stops(db, source_lat, source_lon, radius_km=1.0)
    dest_stops = await find_nearby_gtfs_stops(db, dest_lat, dest_lon, radius_km=1.0)

    if not source_stops or not dest_stops:
        raise HTTPException(
            status_code=404,
            detail="Is area mein GTFS bus stops nahi mile (source ya dest ke 1km radius mein).",
        )

    source_ids = [s["stop_id"] for s in source_stops]
    dest_ids = [s["stop_id"] for s in dest_stops]

    direct_options = await find_direct_bus_options(db, source_ids, dest_ids, after_time=after_time)

    return {
        "nearest_source_stop": source_stops[0],
        "nearest_dest_stop": dest_stops[0],
        "direct_bus_options": direct_options,
        "note": (
            "Yeh real BMTC scheduled timetable hai (live GPS tracking nahi). "
            "Agar koi direct option nahi dikha, matlab in stops ke beech koi seedhi "
            "bus route nahi hai — transfer ke saath jana padega."
        ),
    }