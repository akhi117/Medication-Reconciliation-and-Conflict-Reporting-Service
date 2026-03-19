# app/main.py
# Application entry point. Registers routers and startup/shutdown lifecycle hooks.

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.db import get_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify DB connectivity (fail fast rather than at first request)
    client = get_client()
    await client.admin.command("ping")
    print("✅ Connected to MongoDB")
    yield
    # Shutdown: close the motor client cleanly
    client.close()
    print("🔌 MongoDB connection closed")


app = FastAPI(
    title="Medication Conflict Manager",
    description="Backend system for managing and reconciling medication data across chronic-care sources.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router, prefix="/api/v1")


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok"}
