import uuid
from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.database.connection import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    phone = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)
    travel_level = Column(Integer, default=1)
    trust_score = Column(Float, default=1.0)
    contribution_score = Column(Integer, default=0)
    is_verified_guide = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())