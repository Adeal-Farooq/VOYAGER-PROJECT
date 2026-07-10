import uuid
from sqlalchemy import Column, String, Float, Integer, ForeignKey, DateTime, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.database.connection import Base


class Trip(Base):
    __tablename__ = "trips"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    destination = Column(String, nullable=False)
    total_budget = Column(Float, nullable=False)
    days_count = Column(Integer, nullable=False)
    travel_style = Column(String, nullable=True)
    status = Column(String, default="PLANNING")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TripDay(Base):
    __tablename__ = "trip_days"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trip_id = Column(UUID(as_uuid=True), ForeignKey("trips.id"), nullable=False)
    day_number = Column(Integer, nullable=False)
    total_estimated_cost = Column(Float, default=0.0)
    weather_summary = Column(String, nullable=True)


class TripActivity(Base):
    __tablename__ = "trip_activities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    day_id = Column(UUID(as_uuid=True), ForeignKey("trip_days.id"), nullable=False)
    activity_title = Column(String, nullable=False)
    node_id = Column(BigInteger, ForeignKey("transit_nodes.id"), nullable=True)
    cost_estimation = Column(Float, default=0.0)
    dynamic_sequence = Column(Integer, nullable=False)
    time_slot = Column(String, nullable=False)  # MORNING/AFTERNOON/EVENING/NIGHT