from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.database.connection import engine, Base

# Models import karna zaroori hai taaki Base unhe register kare
from app.models import user, transit, trip, raw_data, gtfs

# Routers
from app.routers import transit as transit_router
from app.routers import routing as routing_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="Travel OS API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(transit_router.router)
app.include_router(routing_router.router)


@app.get("/")
async def root():
    return {"message": "Travel OS backend chal raha hai!"}


@app.get("/health")
async def health_check():
    return {"status": "ok", "database": "connected"}