"""
Travel OS - OSRM Integration
OSRM (Open Source Routing Machine) ka free public demo server use karke
REAL road-following routes nikalta hai — seedhi line nahi, asli sadak ka path.

Source: https://router.project-osrm.org (OpenStreetMap data, free, legit)
Usage policy: max 1 request/second, non-commercial reasonable use.
Attribution zaroori hai: "© OpenStreetMap contributors, routing via OSRM"
"""

import httpx

OSRM_BASE = "https://router.project-osrm.org"


async def get_osrm_route(lat1: float, lon1: float, lat2: float, lon2: float, profile: str = "foot"):
    """
    profile: 'foot' (walking), 'driving' (cab/car), 'bike'
    Returns: {coordinates: [[lon,lat],...], distance_km, duration_min} ya None (agar fail ho)
    """
    url = f"{OSRM_BASE}/route/v1/{profile}/{lon1},{lat1};{lon2},{lat2}"
    params = {"overview": "full", "geometries": "geojson"}
    headers = {"User-Agent": "TravelOS-Bengaluru-TripPlanner/1.0"}

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            data = resp.json()
            if data.get("code") == "Ok" and data.get("routes"):
                route = data["routes"][0]
                return {
                    "coordinates": route["geometry"]["coordinates"],  # [[lon,lat], ...]
                    "distance_km": round(route["distance"] / 1000, 2),
                    "duration_min": round(route["duration"] / 60, 1),
                }
    except Exception:
        pass  # OSRM demo server down ya timeout ho sakta hai — fallback caller handle karega

    return None