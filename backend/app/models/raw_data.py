from sqlalchemy import Column, String, Float, Integer, BigInteger, Date, Time, JSON
from sqlalchemy.dialects.postgresql import JSONB
from app.database.connection import Base


class MetroHourlyRidership(Base):
    """metro.csv se aane wala data — station-wise, hour-slot-wise ridership"""
    __tablename__ = "metro_hourly_ridership"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    business_date = Column(Date, nullable=False)
    station_name = Column(String, nullable=False)
    hour_slot = Column(String, nullable=False)  # e.g. "08:00-09:00"
    passenger_count = Column(Integer, default=0)


class RideBooking(Base):
    """bangalore_ride_data.csv + rides_data.csv dono ko normalize karke yahan daalenge"""
    __tablename__ = "ride_bookings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    source_dataset = Column(String, nullable=False)  # 'bangalore_ride_data' or 'rides_data'
    ride_date = Column(Date, nullable=True)
    ride_time = Column(Time, nullable=True)
    booking_status = Column(String, nullable=True)
    vehicle_type = Column(String, nullable=True)
    pickup_location = Column(String, nullable=True)
    drop_location = Column(String, nullable=True)
    distance_km = Column(Float, nullable=True)
    total_fare = Column(Float, nullable=True)
    payment_method = Column(String, nullable=True)


class AirportRouteFare(Base):
    """kia_routes_fare_full.json se aane wala data"""
    __tablename__ = "airport_route_fares"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    route_code = Column(String, nullable=False)  # e.g. 'KIA-4'
    route_info = Column(String, nullable=True)
    stop_name = Column(String, nullable=False)
    fare = Column(Float, nullable=False)
    stop_sequence = Column(Integer, nullable=False)


class WardTravelTime(Base):
    """bangalore-wards-*.csv se aane wala Uber Movement data (congestion reference ke liye)"""
    __tablename__ = "ward_travel_times"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    source_ward_id = Column(Integer, nullable=False)
    dest_ward_id = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    mean_travel_time = Column(Float, nullable=True)
    std_travel_time = Column(Float, nullable=True)