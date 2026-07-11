from sqlalchemy import Column, String, Float, Integer, BigInteger, Time
from app.database.connection import Base


class GTFSRoute(Base):
    """routes.txt — real bus route numbers"""
    __tablename__ = "gtfs_routes"

    route_id = Column(String, primary_key=True)
    route_short_name = Column(String, nullable=True)  # jaise "244-C VSD"
    route_long_name = Column(String, nullable=True)   # jaise "Nagarabhavi <-> Shivajinagara"
    route_type = Column(Integer, nullable=True)


class GTFSStop(Base):
    """stops.txt — GTFS ke apne stop IDs (humare transit_nodes se alag, inko link karenge naam+location se)"""
    __tablename__ = "gtfs_stops"

    stop_id = Column(String, primary_key=True)
    stop_name = Column(String, nullable=False)
    stop_lat = Column(Float, nullable=False)
    stop_lon = Column(Float, nullable=False)
    zone_id = Column(String, nullable=True)


class GTFSTrip(Base):
    """trips.txt — ek route ka ek specific trip (direction ke saath)"""
    __tablename__ = "gtfs_trips"

    trip_id = Column(String, primary_key=True)
    route_id = Column(String, nullable=False)
    service_id = Column(String, nullable=True)
    trip_headsign = Column(String, nullable=True)
    direction_id = Column(Integer, nullable=True)
    shape_id = Column(String, nullable=True)


class GTFSStopTime(Base):
    """stop_times.txt — REAL SCHEDULED TIMETABLE: kaunsi trip kis stop pe kab pahunchti hai"""
    __tablename__ = "gtfs_stop_times"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    trip_id = Column(String, nullable=False, index=True)
    arrival_time = Column(String, nullable=True)    # GTFS format "25:30:00" bhi ho sakta hai (next-day), isliye String
    departure_time = Column(String, nullable=True)
    stop_id = Column(String, nullable=False, index=True)
    stop_sequence = Column(Integer, nullable=False)


class GTFSShapePoint(Base):
    """shapes.txt — REAL road-following route geometry (seedhi line nahi)"""
    __tablename__ = "gtfs_shape_points"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    shape_id = Column(String, nullable=False, index=True)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    sequence = Column(Integer, nullable=False)


class GTFSFareRule(Base):
    """fare_rules.txt — real fare har origin-destination stop pair ke liye"""
    __tablename__ = "gtfs_fare_rules"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    fare_id = Column(String, nullable=False)
    route_id = Column(String, nullable=True)
    origin_id = Column(String, nullable=True, index=True)
    destination_id = Column(String, nullable=True, index=True)


class GTFSFareAttribute(Base):
    """fare_attributes.txt — fare_id -> actual price mapping"""
    __tablename__ = "gtfs_fare_attributes"

    fare_id = Column(String, primary_key=True)
    price = Column(Float, nullable=False)
    currency_type = Column(String, nullable=True)