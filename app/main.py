# app/main.py
"""
Application entry point and lifecycle management.
This module initializes the FastAPI application, manages database connectivity,
and registers all API routes for the Medication Conflict Manager service.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.db import get_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage the application lifecycle with startup and shutdown events.
    
    This context manager ensures:
    - Database connectivity is verified at startup (fail-fast pattern)
    - Resources are properly cleaned up on shutdown
    
    The yield statement separates startup logic (before) from shutdown logic (after).
    """
    # Startup phase: Verify MongoDB connectivity before accepting requests
    client = get_client()
    await client.admin.command("ping")
    print("✅ Connected to MongoDB")
    
    # Keep the application running
    yield
    
    # Shutdown phase: Gracefully close the database connection
    client.close()
    print("🔌 MongoDB connection closed")

# Initialize the FastAPI application with metadata and lifecycle management
app = FastAPI(
    title="Medication Conflict Manager",
    description="Backend system for managing and reconciling medication data across chronic-care sources.",
    version="1.0.0",
    lifespan=lifespan,  # Enable the lifespan context manager for startup/shutdown events
)

# Register all API routes with a versioned prefix for future compatibility
app.include_router(router, prefix="/api/v1")


@app.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint for monitoring application status.
    
    Returns 200 OK if the service is available and responding.
    Used by load balancers and orchestration platforms (e.g., Kubernetes)
    to determine if the service is healthy and ready to accept requests.
    """
    return {"status": "ok"}