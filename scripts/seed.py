#!/usr/bin/env python3
# scripts/seed.py
# Populates the database with sample patients and medication snapshots
# to demonstrate conflict detection end-to-end.
#
# Usage:
#   python scripts/seed.py
#
# Requires the API to be running (or just a live MongoDB instance).

import asyncio
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import motor.motor_asyncio
from bson import ObjectId
from datetime import datetime, timezone

from app.config import DB_NAME, MONGO_URI
from app.conflict_detection import detect_conflicts
from app.db import (
    conflicts_col,
    get_client,
    insert_many_conflicts,
    insert_snapshot,
    patients_col,
    snapshots_col,
)


async def seed():
    client = get_client()
    db = client[DB_NAME]

    print("🌱 Seeding database...")

    # Clear existing data
    await patients_col().delete_many({})
    await snapshots_col().delete_many({})
    await conflicts_col().delete_many({})
    print("  Cleared existing collections.")

    # --- Create patients ---
    patients = [
        {"name": "Alice Johnson", "clinic_id": "clinic_001", "created_at": datetime.now(timezone.utc)},
        {"name": "Bob Smith", "clinic_id": "clinic_001", "created_at": datetime.now(timezone.utc)},
        {"name": "Carol White", "clinic_id": "clinic_002", "created_at": datetime.now(timezone.utc)},
    ]
    result = await patients_col().insert_many(patients)
    alice_id, bob_id, carol_id = [str(oid) for oid in result.inserted_ids]
    print(f"  Created patients: Alice={alice_id}, Bob={bob_id}, Carol={carol_id}")

    # ---------------------------------------------------------------------------
    # Alice: dose mismatch + status conflict (clinic_emr vs hospital_discharge)
    # ---------------------------------------------------------------------------
    alice_emr_snap = {
        "patient_id": alice_id,
        "source": "clinic_emr",
        "timestamp": datetime.now(timezone.utc),
        "medications": [
            {"name": "metformin", "dose": "500mg", "status": "active"},
            {"name": "lisinopril", "dose": "10mg", "status": "active"},
        ],
    }
    alice_hosp_snap = {
        "patient_id": alice_id,
        "source": "hospital_discharge",
        "timestamp": datetime.now(timezone.utc),
        "medications": [
            {"name": "metformin", "dose": "1000mg", "status": "active"},  # dose mismatch
            {"name": "lisinopril", "dose": "10mg", "status": "stopped"},  # status conflict
        ],
    }

    await insert_snapshot(alice_emr_snap)
    await insert_snapshot(alice_hosp_snap)

    from app.db import get_snapshots_for_patient
    alice_snaps = await get_snapshots_for_patient(alice_id)
    alice_conflicts = detect_conflicts(
        patient_id=alice_id,
        incoming_source="hospital_discharge",
        incoming_meds=alice_hosp_snap["medications"],
        all_snapshots=alice_snaps,
    )
    await insert_many_conflicts(alice_conflicts)
    print(f"  Alice: {len(alice_conflicts)} conflict(s) seeded.")

    # ---------------------------------------------------------------------------
    # Bob: unsafe combination (warfarin + aspirin from different sources)
    # ---------------------------------------------------------------------------
    bob_emr_snap = {
        "patient_id": bob_id,
        "source": "clinic_emr",
        "timestamp": datetime.now(timezone.utc),
        "medications": [
            {"name": "warfarin", "dose": "5mg", "status": "active"},
        ],
    }
    bob_patient_snap = {
        "patient_id": bob_id,
        "source": "patient_reported",
        "timestamp": datetime.now(timezone.utc),
        "medications": [
            {"name": "aspirin", "dose": "81mg", "status": "active"},
        ],
    }

    await insert_snapshot(bob_emr_snap)
    await insert_snapshot(bob_patient_snap)

    bob_snaps = await get_snapshots_for_patient(bob_id)
    bob_conflicts = detect_conflicts(
        patient_id=bob_id,
        incoming_source="patient_reported",
        incoming_meds=bob_patient_snap["medications"],
        all_snapshots=bob_snaps,
    )
    await insert_many_conflicts(bob_conflicts)
    print(f"  Bob: {len(bob_conflicts)} conflict(s) seeded.")

    # ---------------------------------------------------------------------------
    # Carol: no conflicts (same meds across sources)
    # ---------------------------------------------------------------------------
    carol_snap = {
        "patient_id": carol_id,
        "source": "clinic_emr",
        "timestamp": datetime.now(timezone.utc),
        "medications": [
            {"name": "atorvastatin", "dose": "20mg", "status": "active"},
        ],
    }
    await insert_snapshot(carol_snap)
    print(f"  Carol: 0 conflict(s) seeded (clean patient).")

    print("\n✅ Seed complete!")
    print(f"\nTry these API calls:")
    print(f"  GET /api/v1/patients/{alice_id}/conflicts")
    print(f"  GET /api/v1/clinics/clinic_001/patients/conflicts")

    client.close()


if __name__ == "__main__":
    asyncio.run(seed())
