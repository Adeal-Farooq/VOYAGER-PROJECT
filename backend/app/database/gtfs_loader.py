"""
Travel OS - GTFS Data Loader
Real BMTC route/schedule/shape data ko database mein load karta hai
(source: https://github.com/Vonter/bmtc-gtfs)

Chalane se pehle: bmtc.zip extract karke iski saari .txt files
`backend/data_files/gtfs/` folder mein daal do.

Chalane ka tareeka: python -m app.database.gtfs_loader
"""

import asyncio
import pandas as pd

from app.database.connection import AsyncSessionLocal
from app.models.gtfs import (
    GTFSRoute, GTFSStop, GTFSTrip, GTFSStopTime, GTFSShapePoint,
    GTFSFareRule, GTFSFareAttribute,
)
from sqlalchemy import text

GTFS_DIR = "data_files/gtfs"
CHUNK_SIZE = 5000


async def clear_gtfs_tables(session):
    print("Purana GTFS data clear kar raha hu...")
    for table in [
        "gtfs_shape_points", "gtfs_stop_times", "gtfs_trips",
        "gtfs_fare_rules", "gtfs_fare_attributes", "gtfs_stops", "gtfs_routes",
    ]:
        await session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
    await session.commit()
    print("Cleared.\n")


async def load_routes(session):
    print("Loading GTFS routes...")
    df = pd.read_csv(f"{GTFS_DIR}/routes.txt")
    count = 0
    for _, row in df.iterrows():
        session.add(GTFSRoute(
            route_id=str(row["route_id"]),
            route_short_name=str(row["route_short_name"]) if pd.notna(row.get("route_short_name")) else None,
            route_long_name=str(row["route_long_name"]) if pd.notna(row.get("route_long_name")) else None,
            route_type=int(row["route_type"]) if pd.notna(row.get("route_type")) else None,
        ))
        count += 1
    await session.commit()
    print(f"Done: {count} routes.\n")


async def load_stops(session):
    print("Loading GTFS stops...")
    df = pd.read_csv(f"{GTFS_DIR}/stops.txt")
    count = 0
    for _, row in df.iterrows():
        session.add(GTFSStop(
            stop_id=str(row["stop_id"]),
            stop_name=str(row["stop_name"]),
            stop_lat=float(row["stop_lat"]),
            stop_lon=float(row["stop_lon"]),
            zone_id=str(row["zone_id"]) if pd.notna(row.get("zone_id")) else None,
        ))
        count += 1
        if count % CHUNK_SIZE == 0:
            await session.commit()
    await session.commit()
    print(f"Done: {count} stops.\n")


async def load_trips(session):
    print("Loading GTFS trips...")
    count = 0
    for chunk in pd.read_csv(f"{GTFS_DIR}/trips.txt", chunksize=CHUNK_SIZE):
        for _, row in chunk.iterrows():
            session.add(GTFSTrip(
                trip_id=str(row["trip_id"]),
                route_id=str(row["route_id"]),
                service_id=str(row["service_id"]) if pd.notna(row.get("service_id")) else None,
                trip_headsign=str(row["trip_headsign"]) if pd.notna(row.get("trip_headsign")) else None,
                direction_id=int(row["direction_id"]) if pd.notna(row.get("direction_id")) else None,
                shape_id=str(row["shape_id"]) if pd.notna(row.get("shape_id")) else None,
            ))
            count += 1
        await session.commit()
        print(f"  ...{count} trips committed")
    print(f"Done: {count} trips.\n")


async def load_stop_times(session):
    print("Loading GTFS stop_times (yeh sabse bada file hai, thoda time lagega)...")
    count = 0
    for chunk in pd.read_csv(f"{GTFS_DIR}/stop_times.txt", chunksize=CHUNK_SIZE):
        for _, row in chunk.iterrows():
            session.add(GTFSStopTime(
                trip_id=str(row["trip_id"]),
                arrival_time=str(row["arrival_time"]) if pd.notna(row.get("arrival_time")) else None,
                departure_time=str(row["departure_time"]) if pd.notna(row.get("departure_time")) else None,
                stop_id=str(row["stop_id"]),
                stop_sequence=int(row["stop_sequence"]),
            ))
            count += 1
        await session.commit()
        print(f"  ...{count} stop_times committed")
    print(f"Done: {count} stop_times.\n")


async def load_shapes(session):
    print("Loading GTFS shapes (real road-following paths)...")
    count = 0
    for chunk in pd.read_csv(f"{GTFS_DIR}/shapes.txt", chunksize=CHUNK_SIZE):
        for _, row in chunk.iterrows():
            session.add(GTFSShapePoint(
                shape_id=str(row["shape_id"]),
                lat=float(row["shape_pt_lat"]),
                lon=float(row["shape_pt_lon"]),
                sequence=int(row["shape_pt_sequence"]),
            ))
            count += 1
        await session.commit()
        print(f"  ...{count} shape points committed")
    print(f"Done: {count} shape points.\n")


async def load_fares(session):
    print("Loading GTFS fare rules...")
    df_attrs = pd.read_csv(f"{GTFS_DIR}/fare_attributes.txt")
    for _, row in df_attrs.iterrows():
        session.add(GTFSFareAttribute(
            fare_id=str(row["fare_id"]),
            price=float(row["price"]),
            currency_type=str(row.get("currency_type", "INR")),
        ))
    await session.commit()
    print(f"  {len(df_attrs)} fare attributes loaded")

    count = 0
    for chunk in pd.read_csv(f"{GTFS_DIR}/fare_rules.txt", chunksize=CHUNK_SIZE):
        for _, row in chunk.iterrows():
            session.add(GTFSFareRule(
                fare_id=str(row["fare_id"]),
                route_id=str(row["route_id"]) if pd.notna(row.get("route_id")) else None,
                origin_id=str(row["origin_id"]) if pd.notna(row.get("origin_id")) else None,
                destination_id=str(row["destination_id"]) if pd.notna(row.get("destination_id")) else None,
            ))
            count += 1
        await session.commit()
        print(f"  ...{count} fare rules committed")
    print(f"Done: {count} fare rules.\n")


async def main():
    async with AsyncSessionLocal() as session:
        await clear_gtfs_tables(session)
        await load_routes(session)
        await load_stops(session)
        await load_trips(session)
        await load_stop_times(session)
        await load_shapes(session)
        await load_fares(session)

    print("=== GTFS DATA SUCCESSFULLY LOAD HO GAYA ===")


if __name__ == "__main__":
    asyncio.run(main())