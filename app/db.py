# app/db.py
# Database access layer. All MongoDB interaction lives here.
# Motor is used for async I/O. Collections are accessed via a module-level
# client so the connection is reused across requests.

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

# ---------------------------------------------------------------------------
# Connection setup
# ---------------------------------------------------------------------------

_client: motor.motor_asyncio.AsyncIOMotorClient | None = None


def get_client() -> motor.motor_asyncio.AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
    return _client


def get_db() -> motor.motor_asyncio.AsyncIOMotorDatabase:
    return get_client()[DB_NAME]


def patients_col():
    return get_db()[COLLECTION_PATIENTS]


def snapshots_col():
    return get_db()[COLLECTION_SNAPSHOTS]


def conflicts_col():
    return get_db()[COLLECTION_CONFLICTS]


# ---------------------------------------------------------------------------
# Patient helpers
# ---------------------------------------------------------------------------

async def get_patient(patient_id: str) -> dict | None:
    """Return patient doc or None if not found / invalid id."""
    try:
        oid = ObjectId(patient_id)
    except Exception:
        return None
    return await patients_col().find_one({"_id": oid})


async def get_patient_by_clinic(clinic_id: str) -> list[dict]:
    cursor = patients_col().find({"clinic_id": clinic_id})
    return await cursor.to_list(length=None)


async def create_patient(name: str, clinic_id: str) -> str:
    doc = {
        "name": name,
        "clinic_id": clinic_id,
        "created_at": datetime.now(timezone.utc),
    }
    result = await patients_col().insert_one(doc)
    return str(result.inserted_id)


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

async def insert_snapshot(snapshot: dict) -> str:
    result = await snapshots_col().insert_one(snapshot)
    return str(result.inserted_id)


async def get_snapshots_for_patient(patient_id: str) -> list[dict]:
    """All historical snapshots for a patient, oldest first."""
    cursor = snapshots_col().find(
        {"patient_id": patient_id},
        sort=[("timestamp", 1)],
    )
    return await cursor.to_list(length=None)


# ---------------------------------------------------------------------------
# Conflict helpers
# ---------------------------------------------------------------------------

async def insert_conflict(conflict: dict) -> str:
    result = await conflicts_col().insert_one(conflict)
    return str(result.inserted_id)


async def insert_many_conflicts(conflicts: list[dict]) -> int:
    if not conflicts:
        return 0
    result = await conflicts_col().insert_many(conflicts)
    return len(result.inserted_ids)


async def get_unresolved_conflicts_for_patient(patient_id: str) -> list[dict]:
    cursor = conflicts_col().find(
        {"patient_id": patient_id, "status": "unresolved"}
    )
    return await cursor.to_list(length=None)


async def get_conflict(conflict_id: str) -> dict | None:
    try:
        oid = ObjectId(conflict_id)
    except Exception:
        return None
    return await conflicts_col().find_one({"_id": oid})


async def resolve_conflict(conflict_id: str, resolution: dict) -> bool:
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
                    **resolution,
                    "resolved_at": datetime.now(timezone.utc),
                },
            }
        },
    )
    return result.modified_count == 1


async def count_unresolved_conflicts_by_patient(patient_ids: list[str]) -> dict[str, int]:
    """
    Returns {patient_id: unresolved_count} for the given patient IDs.
    Uses an aggregation pipeline for efficiency instead of N queries.
    """
    if not patient_ids:
        return {}

    pipeline = [
        {"$match": {"patient_id": {"$in": patient_ids}, "status": "unresolved"}},
        {"$group": {"_id": "$patient_id", "count": {"$sum": 1}}},
    ]
    cursor = conflicts_col().aggregate(pipeline)
    results = await cursor.to_list(length=None)
    return {row["_id"]: row["count"] for row in results}
