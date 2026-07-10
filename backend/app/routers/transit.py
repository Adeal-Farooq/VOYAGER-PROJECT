from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from geoalchemy2.functions import ST_X, ST_Y
from typing import Optional

from app.database.connection import get_db
from app.models.transit import TransitNode

router = APIRouter(prefix="/api/transit", tags=["transit"])


@router.get("/nodes")
async def get_transit_nodes(
    node_type: Optional[str] = Query(
        None, description="Filter by type: BMTC_BUS_STOP or METRO_STATION"
    ),
    limit: int = Query(500, le=5000, description="Max number of nodes to return"),
    db: AsyncSession = Depends(get_db),
):
    """
    Saare transit nodes (bus stops + metro stations) return karta hai,
    map pe dikhane ke liye lat/long ke saath.
    """
    query = select(
        TransitNode.id,
        TransitNode.name,
        TransitNode.type,
        TransitNode.code,
        ST_Y(TransitNode.location).label("latitude"),
        ST_X(TransitNode.location).label("longitude"),
        TransitNode.metadata_json,
    )

    if node_type:
        query = query.where(TransitNode.type == node_type)

    query = query.limit(limit)

    result = await db.execute(query)
    rows = result.all()

    return {
        "count": len(rows),
        "nodes": [
            {
                "id": row.id,
                "name": row.name,
                "type": row.type,
                "code": row.code,
                "latitude": row.latitude,
                "longitude": row.longitude,
                "metadata": row.metadata_json,
            }
            for row in rows
        ],
    }


@router.get("/nodes/bbox")
async def get_nodes_in_bounding_box(
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    node_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Ek map bounding box (jo user ko screen pe dikh raha hai) ke andar
    aane wale saare transit nodes return karta hai.
    Yeh production mein zyada efficient hai kyunki poora data ek saath load nahi hota.
    """
    query = select(
        TransitNode.id,
        TransitNode.name,
        TransitNode.type,
        TransitNode.code,
        ST_Y(TransitNode.location).label("latitude"),
        ST_X(TransitNode.location).label("longitude"),
    ).where(
        ST_Y(TransitNode.location).between(min_lat, max_lat),
        ST_X(TransitNode.location).between(min_lon, max_lon),
    )

    if node_type:
        query = query.where(TransitNode.type == node_type)

    result = await db.execute(query.limit(2000))
    rows = result.all()

    return {
        "count": len(rows),
        "nodes": [
            {
                "id": row.id,
                "name": row.name,
                "type": row.type,
                "code": row.code,
                "latitude": row.latitude,
                "longitude": row.longitude,
            }
            for row in rows
        ],
    }


@router.get("/stats")
async def get_transit_stats(db: AsyncSession = Depends(get_db)):
    """Quick summary — kitne bus stops, kitne metro stations hain"""
    query = select(TransitNode.type, func.count(TransitNode.id)).group_by(TransitNode.type)
    result = await db.execute(query)
    rows = result.all()

    return {row.type: row[1] for row in rows}