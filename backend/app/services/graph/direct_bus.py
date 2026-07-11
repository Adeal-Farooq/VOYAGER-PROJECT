"""
Travel OS - Real GTFS-based Direct Bus Finder

Yeh module asli BMTC schedule data (GTFS) use karke batata hai:
- Kaunsi bus number(s) direct jaati hain source se destination tak
- Kis scheduled time pe milegi
- Kitne stops hain beech mein
- Real fare (agar fare_rules mein data mile)

Yeh "approximation" nahi hai — yeh real BMTC route/timetable data hai.
"""

import math
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


async def find_nearby_gtfs_stops(session: AsyncSession, lat: float, lon: float, radius_km: float = 1.0):
    """Diye gaye point ke around GTFS stops dhoondta hai"""
    buffer_deg = radius_km / 111.0  # rough km-to-degree conversion
    query = text("""
        SELECT stop_id, stop_name, stop_lat, stop_lon
        FROM gtfs_stops
        WHERE stop_lat BETWEEN :min_lat AND :max_lat
          AND stop_lon BETWEEN :min_lon AND :max_lon
    """)
    result = await session.execute(query, {
        "min_lat": lat - buffer_deg, "max_lat": lat + buffer_deg,
        "min_lon": lon - buffer_deg, "max_lon": lon + buffer_deg,
    })
    rows = result.all()

    nearby = []
    for row in rows:
        d = haversine_km(lat, lon, row.stop_lat, row.stop_lon)
        if d <= radius_km:
            nearby.append({"stop_id": row.stop_id, "stop_name": row.stop_name, "distance_km": round(d, 2)})

    nearby.sort(key=lambda x: x["distance_km"])
    return nearby[:15]  # sirf sabse nazdeeki 15 stops


async def get_avg_fare_per_km(session: AsyncSession) -> float:
    """
    Existing fare_rules data se average fare/km rate nikalta hai (sample ke through) —
    isse hum un stop-pairs ke liye bhi realistic fare estimate de sakte hain jinke
    liye exact fare_rules match nahi milta.
    """
    query = text("""
        SELECT fa.price, s1.stop_lat as lat1, s1.stop_lon as lon1,
               s2.stop_lat as lat2, s2.stop_lon as lon2
        FROM gtfs_fare_rules fr
        JOIN gtfs_fare_attributes fa ON fr.fare_id = fa.fare_id
        JOIN gtfs_stops s1 ON fr.origin_id = s1.stop_id
        JOIN gtfs_stops s2 ON fr.destination_id = s2.stop_id
        LIMIT 3000
    """)
    result = await session.execute(query)
    rows = result.all()

    rates = []
    for row in rows:
        d = haversine_km(row.lat1, row.lon1, row.lat2, row.lon2)
        if d > 0.3:  # bahut chote distance rate ko skew kar sakte hain, unhe skip karo
            rates.append(row.price / d)

    if not rates:
        return 1.8  # BMTC ordinary bus ka typical fallback rate (₹/km)

    rates.sort()
    return rates[len(rates) // 2]  # median — outliers se zyada robust


async def find_direct_bus_options(
    session: AsyncSession,
    source_stop_ids: list[str],
    dest_stop_ids: list[str],
    max_results: int = 8,
):
    """
    Real GTFS data mein dhoondta hai — kaunsi trips hain jo source stops se
    hoke dest stops tak seedhi (direct, bina transfer ke) jaati hain.
    """
    if not source_stop_ids or not dest_stop_ids:
        return []

    query = text("""
        SELECT
            r.route_short_name,
            r.route_long_name,
            t.trip_headsign,
            st1.stop_id as from_stop_id,
            st2.stop_id as to_stop_id,
            st1.departure_time,
            st2.arrival_time,
            (st2.stop_sequence - st1.stop_sequence) as stops_between,
            s1.stop_lat as from_lat, s1.stop_lon as from_lon,
            s2.stop_lat as to_lat, s2.stop_lon as to_lon
        FROM gtfs_stop_times st1
        JOIN gtfs_stop_times st2
            ON st1.trip_id = st2.trip_id
            AND st1.stop_sequence < st2.stop_sequence
        JOIN gtfs_trips t ON st1.trip_id = t.trip_id
        JOIN gtfs_routes r ON t.route_id = r.route_id
        JOIN gtfs_stops s1 ON st1.stop_id = s1.stop_id
        JOIN gtfs_stops s2 ON st2.stop_id = s2.stop_id
        WHERE st1.stop_id = ANY(:source_ids)
          AND st2.stop_id = ANY(:dest_ids)
        ORDER BY st1.departure_time
        LIMIT :limit
    """)
    result = await session.execute(query, {
        "source_ids": source_stop_ids,
        "dest_ids": dest_stop_ids,
        "limit": max_results * 3,  # extra fetch karo, phir dedupe karenge
    })
    rows = result.all()

    avg_fare_per_km = await get_avg_fare_per_km(session)

    # Same route number ke multiple trips ko dedupe karo (sirf pehla upcoming dikhana hai)
    seen_routes = set()
    options = []
    for row in rows:
        route_key = row.route_short_name
        if route_key in seen_routes:
            continue
        seen_routes.add(route_key)

        # Pehle EXACT fare dhoondo fare_rules se
        fare_query = text("""
            SELECT fa.price FROM gtfs_fare_rules fr
            JOIN gtfs_fare_attributes fa ON fr.fare_id = fa.fare_id
            WHERE fr.origin_id = :origin AND fr.destination_id = :dest
            LIMIT 1
        """)
        fare_result = await session.execute(fare_query, {"origin": row.from_stop_id, "dest": row.to_stop_id})
        fare_row = fare_result.first()

        if fare_row:
            fare = float(fare_row.price)
            fare_is_estimated = False
        else:
            # Exact match nahi mila -> distance-based estimate do (Google Maps bhi yahi karta hai)
            distance_km = haversine_km(row.from_lat, row.from_lon, row.to_lat, row.to_lon)
            fare = round(max(5.0, distance_km * avg_fare_per_km), 2)
            fare_is_estimated = True

        options.append({
            "route_number": row.route_short_name,
            "route_name": row.route_long_name,
            "headsign": row.trip_headsign,
            "departure_time": row.departure_time,
            "arrival_time": row.arrival_time,
            "stops_between": row.stops_between,
            "fare": fare,
            "fare_is_estimated": fare_is_estimated,
        })
        if len(options) >= max_results:
            break

    return options