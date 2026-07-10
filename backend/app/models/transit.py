from sqlalchemy import Column, String, Float, BigInteger, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from geoalchemy2 import Geometry
from app.database.connection import Base


class TransitNode(Base):
    __tablename__ = "transit_nodes"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # 'BMTC_BUS_STOP' or 'METRO_STATION'
    code = Column(String, nullable=True)
    location = Column(Geometry(geometry_type="POINT", srid=4326), nullable=False)
    metadata_json = Column(JSONB, nullable=True)


class RouteSegment(Base):
    __tablename__ = "route_segments"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    source_node_id = Column(BigInteger, ForeignKey("transit_nodes.id"), nullable=False)
    target_node_id = Column(BigInteger, ForeignKey("transit_nodes.id"), nullable=False)
    transit_type = Column(String, nullable=False)
    segment_geometry = Column(Geometry(geometry_type="LINESTRING", srid=4326), nullable=True)
    spatial_distance = Column(Float, nullable=True)
    baseline_fare = Column(Float, nullable=True)