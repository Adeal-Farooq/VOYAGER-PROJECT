"""
Travel OS - Data Ingestion Loader
Yeh script saare CSV/JSON files ko padhta hai aur PostGIS database mein daalta hai.

Chalane ka tareeka (backend folder se, venv activated):
    python -m app.database.loaders
"""

import asyncio
import json
import ast
from datetime import datetime

import pandas as pd
from sqlalchemy import text

from app.database.connection import AsyncSessionLocal
from app.models.transit import TransitNode, RouteSegment
from app.models.raw_data import (
    MetroHourlyRidership,
    RideBooking,
    AirportRouteFare,
    WardTravelTime,
)

DATA_DIR = "data_files"
CHUNK_SIZE = 5000


# ------------------------------------------------------------------
# 1. BMTC Bus Stops -> transit_nodes
# ------------------------------------------------------------------
async def load_bmtc_stops(session):
    print("Loading BMTC bus stops...")
    df = pd.read_csv(f"{DATA_DIR}/bmtc_all_stops_master.csv")
    df = df.dropna(subset=["Stop Name", "Latitude", "Longitude"])

    count = 0
    for _, row in df.iterrows():
        num_trips = row.get("Num trips in stop")
        boothcode = row.get("Boothcode")

        # "Routes with num trips" column ek Python-dict-jaisi string hai, jaise "{'242-LA': 8}"
        routes_raw = row.get("Routes with num trips")
        route_numbers = []
        if pd.notna(routes_raw):
            try:
                parsed = ast.literal_eval(str(routes_raw))
                if isinstance(parsed, dict):
                    route_numbers = list(parsed.keys())
            except (ValueError, SyntaxError):
                route_numbers = []

        metadata = {
            "num_trips": float(num_trips) if pd.notna(num_trips) else None,
            "boothcode": str(boothcode) if pd.notna(boothcode) else None,
            "routes": route_numbers,
        }
        node = TransitNode(
            name=str(row["Stop Name"]),
            type="BMTC_BUS_STOP",
            code=str(boothcode) if pd.notna(boothcode) else None,
            location=f"SRID=4326;POINT({row['Longitude']} {row['Latitude']})",
            metadata_json=metadata,
        )
        session.add(node)
        count += 1
        if count % CHUNK_SIZE == 0:
            await session.commit()
            print(f"  ...{count} stops committed")

    await session.commit()
    print(f"Done: {count} BMTC stops loaded.\n")


# ------------------------------------------------------------------
# 2. Metro Network -> transit_nodes + route_segments
# ------------------------------------------------------------------
async def load_metro_network(session):
    print("Loading Metro network...")
    df = pd.read_csv(f"{DATA_DIR}/bengaluru_metro_network.csv")

    code_to_id = {}

    # Pehle saare stations ko transit_nodes mein daalo
    for _, row in df.iterrows():
        metadata = {
            "line": row["line"],
            "sequence": int(row["sequence"]),
            "is_interchange": bool(row["is_interchange"]),
            "line_color": row["line_color"],
        }
        node = TransitNode(
            name=str(row["station_name"]),
            type="METRO_STATION",
            code=str(row["station_code"]),
            location=f"SRID=4326;POINT({row['longitude']} {row['latitude']})",
            metadata_json=metadata,
        )
        session.add(node)
        await session.flush()  # id turant chahiye
        code_to_id[row["station_code"]] = node.id

    await session.commit()
    print(f"  {len(code_to_id)} metro stations loaded.")

    # Ab route_segments banao (station -> next_station)
    seg_count = 0
    for _, row in df.iterrows():
        next_code = row.get("next_station_code")
        if pd.isna(next_code) or next_code not in code_to_id:
            continue
        segment = RouteSegment(
            source_node_id=code_to_id[row["station_code"]],
            target_node_id=code_to_id[next_code],
            transit_type="METRO",
            spatial_distance=float(row["distance_to_next_km"]) * 1000,  # km -> meters
            baseline_fare=None,
        )
        session.add(segment)
        seg_count += 1

    await session.commit()
    print(f"Done: {seg_count} metro route segments loaded.\n")


# ------------------------------------------------------------------
# 3. Metro hourly ridership (metro.csv)
# ------------------------------------------------------------------
async def load_metro_hourly_ridership(session):
    print("Loading metro hourly ridership...")
    df = pd.read_csv(f"{DATA_DIR}/metro.csv")

    hour_cols = [c for c in df.columns if "Hrs" in c]
    count = 0
    for _, row in df.iterrows():
        business_date = pd.to_datetime(row["BUSINESS DATE"]).date()
        station = str(row["STATION"])
        for col in hour_cols:
            value = row[col]
            if pd.isna(value):
                continue
            session.add(
                MetroHourlyRidership(
                    business_date=business_date,
                    station_name=station,
                    hour_slot=col.strip(),
                    passenger_count=int(value),
                )
            )
            count += 1
        if count % CHUNK_SIZE < 30:  # periodic commit
            await session.commit()

    await session.commit()
    print(f"Done: {count} hourly ridership records loaded.\n")


# ------------------------------------------------------------------
# 4. Ride bookings (bangalore_ride_data.csv + rides_data.csv)
# ------------------------------------------------------------------
async def load_bangalore_ride_data(session):
    print("Loading bangalore_ride_data.csv...")
    count = 0
    for chunk in pd.read_csv(f"{DATA_DIR}/bangalore_ride_data.csv", chunksize=CHUNK_SIZE):
        for _, row in chunk.iterrows():
            try:
                ride_date = pd.to_datetime(row["Date"]).date() if pd.notna(row["Date"]) else None
                ride_time = (
                    datetime.strptime(str(row["Time"]), "%H:%M:%S").time()
                    if pd.notna(row["Time"])
                    else None
                )
            except (ValueError, TypeError):
                ride_date, ride_time = None, None

            def clean_str(val):
                return str(val) if pd.notna(val) else None

            def clean_float(val):
                return float(val) if pd.notna(val) else None

            session.add(
                RideBooking(
                    source_dataset="bangalore_ride_data",
                    ride_date=ride_date,
                    ride_time=ride_time,
                    booking_status=clean_str(row.get("Booking Status")),
                    vehicle_type=clean_str(row.get("Vehicle Type")),
                    pickup_location=clean_str(row.get("Pickup Location")),
                    drop_location=clean_str(row.get("Drop Location")),
                    distance_km=clean_float(row.get("Ride Distance")),
                    total_fare=clean_float(row.get("Booking Value")),
                    payment_method=clean_str(row.get("Payment Method")),
                )
            )
            count += 1
        await session.commit()
        print(f"  ...{count} rows committed")

    print(f"Done: {count} bangalore_ride_data rows loaded.\n")


async def load_rides_data(session):
    print("Loading rides_data.csv...")
    count = 0
    for chunk in pd.read_csv(f"{DATA_DIR}/rides_data.csv", chunksize=CHUNK_SIZE):
        for _, row in chunk.iterrows():
            try:
                ride_date = pd.to_datetime(row["date"]).date() if pd.notna(row["date"]) else None
                ride_time = (
                    datetime.strptime(str(row["time"]).split(".")[0], "%H:%M:%S").time()
                    if pd.notna(row["time"])
                    else None
                )
            except (ValueError, TypeError):
                ride_date, ride_time = None, None

            def clean_str(val):
                return str(val) if pd.notna(val) else None

            def clean_float(val):
                return float(val) if pd.notna(val) else None

            session.add(
                RideBooking(
                    source_dataset="rides_data",
                    ride_date=ride_date,
                    ride_time=ride_time,
                    booking_status=clean_str(row.get("ride_status")),
                    vehicle_type=clean_str(row.get("services")),
                    pickup_location=clean_str(row.get("source")),
                    drop_location=clean_str(row.get("destination")),
                    distance_km=clean_float(row.get("distance")),
                    total_fare=clean_float(row.get("total_fare")),
                    payment_method=clean_str(row.get("payment_method")),
                )
            )
            count += 1
        await session.commit()
        print(f"  ...{count} rows committed")

    print(f"Done: {count} rides_data rows loaded.\n")


# ------------------------------------------------------------------
# 5. Airport route fares (kia_routes_fare_full.json)
# ------------------------------------------------------------------
async def load_airport_fares(session):
    print("Loading airport route fares...")
    with open(f"{DATA_DIR}/kia_routes_fare_full.json") as f:
        data = json.load(f)

    routes = data.get("vayu_vajra_kia_routes", {})
    count = 0
    for route_code, route_data in routes.items():
        for idx, stop in enumerate(route_data.get("stops", [])):
            session.add(
                AirportRouteFare(
                    route_code=route_code,
                    route_info=route_data.get("route_info"),
                    stop_name=stop["stop_name"],
                    fare=float(stop["fare"]),
                    stop_sequence=idx,
                )
            )
            count += 1

    await session.commit()
    print(f"Done: {count} airport fare records loaded.\n")


# ------------------------------------------------------------------
# 6. Ward travel times (bangalore-wards-*.csv) - congestion reference data
# ------------------------------------------------------------------
async def load_ward_travel_times(session):
    print("Loading ward travel time data...")
    files = [
        "bangalore-wards-2018-1-All-MonthlyAggregate.csv",
        "bangalore-wards-2018-2-All-MonthlyAggregate.csv",
        "bangalore-wards-2018-3-All-MonthlyAggregate.csv",
        "bangalore-wards-2018-4-All-MonthlyAggregate.csv",
    ]
    total = 0
    for fname in files:
        count = 0
        for chunk in pd.read_csv(f"{DATA_DIR}/{fname}", chunksize=CHUNK_SIZE):
            for _, row in chunk.iterrows():
                session.add(
                    WardTravelTime(
                        source_ward_id=int(row["sourceid"]),
                        dest_ward_id=int(row["dstid"]),
                        month=int(row["month"]),
                        mean_travel_time=row["mean_travel_time"],
                        std_travel_time=row["standard_deviation_travel_time"],
                    )
                )
                count += 1
            await session.commit()
        total += count
        print(f"  {fname}: {count} rows loaded")

    print(f"Done: {total} ward travel-time rows loaded.\n")


# ------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------
async def clear_existing_data(session):
    """Pehle se loaded data clear karo taaki dobara chalane pe duplicate na bane"""
    print("Clearing old data before fresh load...")
    await session.execute(text("TRUNCATE TABLE route_segments RESTART IDENTITY CASCADE"))
    await session.execute(text("TRUNCATE TABLE transit_nodes RESTART IDENTITY CASCADE"))
    await session.execute(text("TRUNCATE TABLE metro_hourly_ridership RESTART IDENTITY CASCADE"))
    await session.execute(text("TRUNCATE TABLE ride_bookings RESTART IDENTITY CASCADE"))
    await session.execute(text("TRUNCATE TABLE airport_route_fares RESTART IDENTITY CASCADE"))
    await session.execute(text("TRUNCATE TABLE ward_travel_times RESTART IDENTITY CASCADE"))
    await session.commit()
    print("Old data cleared.\n")


async def main():
    async with AsyncSessionLocal() as session:
        await clear_existing_data(session)
        await load_bmtc_stops(session)
        await load_metro_network(session)
        await load_metro_hourly_ridership(session)
        await load_bangalore_ride_data(session)
        await load_rides_data(session)
        await load_airport_fares(session)
        await load_ward_travel_times(session)

    print("=== SAB DATA SUCCESSFULLY LOAD HO GAYA ===")


if __name__ == "__main__":
    asyncio.run(main())