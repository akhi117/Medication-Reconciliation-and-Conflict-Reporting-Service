# app/db.py
"""
Database access layer for MongoDB operations.

This module provides an abstraction for all database interactions, ensuring:
- A single reusable connection (Motor client singleton) across all requests
- Async/await support via Motor for non-blocking I/O
- Consistent error handling and type conversion (e.g., ObjectId validation)
- Organized helper functions grouped by entity (patients, snapshots, conflicts)

The module uses Motor (async MongoDB driver) to prevent blocking the event loop
during database operations, which is critical for FastAPI's async concurrency.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import motor.motor_asyncio
from bson import ObjectId

from app.config import (
    COLLECTION_CONFLICTS,
    COLLECTION_PATIENTS,
    COLLECTION_SNAPSHOTS,
    DB_NAME,
    MONGO_URI,
)

# Connection management using singleton pattern
# We maintain a single MongoDB connection throughout the application lifetime
# to avoid creating new connections for every request (which would be slow and wasteful)

_client: motor.motor_asyncio.AsyncIOMotorClient | None = None


def get_client() -> motor.motor_asyncio.AsyncIOMotorClient:
    """
    Retrieve or initialize the MongoDB client using singleton pattern.
    
    Returns the same client instance for all requests, avoiding the overhead
    of creating new connections. The connection is created lazily on first use
    and kept alive via the lifespan context manager in main.py.
    """
    global _client
    if _client is None:
        _client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
    return _client


def get_db() -> motor.motor_asyncio.AsyncIOMotorDatabase:
    """Access the configured database within the connected client."""
    return get_client()[DB_NAME]


def patients_col():
    """Access the patients collection (stores patient information and clinic associations)."""
    return get_db()[COLLECTION_PATIENTS]


def snapshots_col():
    """
    Access the snapshots collection.
    Stores historical medication records from various sources for conflict detection.
    """
    return get_db()[COLLECTION_SNAPSHOTS]


def conflicts_col():
    """Access the conflicts collection (tracks detected and resolved medication conflicts)."""
    return get_db()[COLLECTION_CONFLICTS]


# ============================================================================
# Patient Operations
# ============================================================================
# Functions for querying and managing patient records

async def get_patient(patient_id: str) -> dict | None:
    """
    Retrieve a patient document by ID.
    
    Safely handles invalid ObjectId formats by catching exceptions and returning None.
    This prevents server errors when clients pass malformed IDs.
    
    Args:
        patient_id: String representation of MongoDB ObjectId
    
    Returns:
        Patient document dict if found, None if not found or invalid ID
    """
    try:
        oid = ObjectId(patient_id)
    except Exception:
        return None
    return await patients_col().find_one({"_id": oid})


async def get_patient_by_clinic(clinic_id: str) -> list[dict]:
    """
    Retrieve all patients associated with a specific clinic.
    
    Uses a cursor pattern for efficient querying of potentially large result sets.
    Length=None means retrieve all matching documents.
    """
    cursor = patients_col().find({"clinic_id": clinic_id})
    return await cursor.to_list(length=None)


async def create_patient(name: str, clinic_id: str) -> str:
    """
    Create a new patient record with current UTC timestamp.
    
    MongoDB automatically generates an _id field, which we convert to string
    for JSON serialization and easier client handling.
    
    Args:
        name: Patient's name
        clinic_id: Associated clinic identifier
    
    Returns:
        The newly created patient's MongoDB ObjectId as a string
    """
    doc = {
        "name": name,
        "clinic_id": clinic_id,
        "created_at": datetime.now(timezone.utc),
    }
    result = await patients_col().insert_one(doc)
    return str(result.inserted_id)


# ============================================================================
# Snapshot Operations
# ============================================================================
# Functions for storing and retrieving medication snapshots from various sources

async def insert_snapshot(snapshot: dict) -> str:
    """
    Create a new medication snapshot record.
    
    Snapshots are immutable historical records of a patient's medication data
    from external sources (pharmacy, EHR systems, etc.).
    
    Returns:
        The newly created snapshot's MongoDB ObjectId as a string
    """
    result = await snapshots_col().insert_one(snapshot)
    return str(result.inserted_id)


async def get_snapshots_for_patient(patient_id: str) -> list[dict]:
    """
    Retrieve all historical snapshots for a patient, sorted by timestamp (oldest first).
    
    The chronological ordering (ascending by timestamp) allows conflict detection
    algorithms to properly track how medication data changes over time.
    
    Returns:
        List of snapshot documents sorted from oldest to newest
    """
    cursor = snapshots_col().find(
        {"patient_id": patient_id},
        sort=[("timestamp", 1)],  # 1 = ascending order (oldest first)
    )
    return await cursor.to_list(length=None)


# ============================================================================
# Conflict Operations
# ============================================================================
# Functions for managing conflict detection results and resolutions

async def insert_conflict(conflict: dict) -> str:
    """
    Create a new conflict record (typically generated by conflict detection logic).
    
    Conflicts capture medication discrepancies identified across data sources.
    
    Returns:
        The newly created conflict's MongoDB ObjectId as a string
    """
    result = await conflicts_col().insert_one(conflict)
    return str(result.inserted_id)


async def insert_many_conflicts(conflicts: list[dict]) -> int:
    """
    Batch insert multiple conflict records in a single database operation.
    
    This is more efficient than inserting conflicts one-by-one when the conflict
    detection engine produces multiple conflicts at once.
    
    Args:
        conflicts: List of conflict documents to insert
    
    Returns:
        The number of conflicts successfully inserted
    """
    if not conflicts:
        return 0
    result = await conflicts_col().insert_many(conflicts)
    return len(result.inserted_ids)


async def get_unresolved_conflicts_for_patient(patient_id: str) -> list[dict]:
    """
    Retrieve all unresolved conflicts for a specific patient.
    
    Used by the UI and reporting systems to show pending medication conflicts
    that require clinical review and resolution.
    
    Returns:
        List of unresolved conflict documents for the patient
    """
    cursor = conflicts_col().find(
        {"patient_id": patient_id, "status": "unresolved"}
    )
    return await cursor.to_list(length=None)


async def get_conflict(conflict_id: str) -> dict | None:
    """
    Retrieve a specific conflict document by ID.
    
    Safely handles invalid ObjectId formats by catching exceptions and returning None.
    
    Args:
        conflict_id: String representation of the conflict's MongoDB ObjectId
    
    Returns:
        Conflict document if found, None if not found or invalid ID
    """
    try:
        oid = ObjectId(conflict_id)
    except Exception:
        return None
    return await conflicts_col().find_one({"_id": oid})


async def resolve_conflict(conflict_id: str, resolution: dict) -> bool:
    """
    Mark a conflict as resolved and store the resolution details.
    
    This updates the conflict document with a resolution explanation and timestamp.
    Used when a clinician or system has reviewed and decided how to handle a conflict.
    
    Args:
        conflict_id: String representation of the conflict's MongoDB ObjectId
        resolution: Dict containing resolution details (e.g., chosen medication, notes)
    
    Returns:
        True if the conflict was successfully resolved (exactly 1 doc modified),
        False if the operation failed or conflict_id was invalid
    """
    try:
        oid = ObjectId(conflict_id)
    except Exception:
        return False
    result = await conflicts_col().update_one(
        {"_id": oid},
        {
            "$set": {
                "status": "resolved",
                "resolution": {
                    **resolution,  # Include provided resolution details
                    "resolved_at": datetime.now(timezone.utc),  # Record when it was resolved
                },
            }
        },
    )
    # Verify exactly one document was modified (conflict existed and was updated)
    return result.modified_count == 1


async def count_unresolved_conflicts_by_patient(patient_ids: list[str]) -> dict[str, int]:
    """
    Count unresolved conflicts for multiple patients in a single database operation.
    
    Returns a mapping of patient_id to unresolved conflict count.
    This uses MongoDB's aggregation framework, which is more efficient than querying
    each patient individually (N+1 query problem). Perfect for dashboards showing
    how many conflicts need attention per patient.
    
    Args:
        patient_ids: List of patient IDs to aggregate counts for
    
    Returns:
        Dictionary mapping patient_id to unresolved conflict count
        (patients with 0 unresolved conflicts are not included)
    """
    if not patient_ids:
        return {}

    # MongoDB aggregation pipeline:
    # Stage 1: Filter to only unresolved conflicts for the given patients
    # Stage 2: Group by patient_id and count documents in each group
    pipeline = [
        {
            "$match": {
                "patient_id": {"$in": patient_ids},  # Only these patients
                "status": "unresolved"  # Only unresolved conflicts
            }
        },
        {
            "$group": {
                "_id": "$patient_id",  # Group by patient_id
                "count": {"$sum": 1}  # Count documents in each group
            }
        },
    ]
    cursor = conflicts_col().aggregate(pipeline)
    results = await cursor.to_list(length=None)
    # Transform [{_id: patient_id, count: n}, ...] into {patient_id: n, ...}
    return {row["_id"]: row["count"] for row in results}
