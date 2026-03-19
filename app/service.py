# app/service.py
# Service layer: orchestrates DB access, normalization, and conflict detection.
# Routes call service functions; services never import from routes.

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException

from app import db
from app.conflict_detection import detect_conflicts
from app.models import (
    ClinicConflictsResponse,
    ConflictResolution,
    IngestMedicationsRequest,
    IngestMedicationsResponse,
    PatientConflictSummary,
)
from app.utils import normalize_medications


# ---------------------------------------------------------------------------
# Ingestion service
# ---------------------------------------------------------------------------

async def ingest_medications(
    patient_id: str,
    payload: IngestMedicationsRequest,
) -> IngestMedicationsResponse:
    """
    1. Verify patient exists.
    2. Normalize medications.
    3. Store new snapshot (append-only).
    4. Load all snapshots for this patient.
    5. Run conflict detection.
    6. Persist any new conflicts.
    """
    # Guard: patient must exist
    patient = await db.get_patient(patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail=f"Patient '{patient_id}' not found")

    # Normalize input
    normalized_meds = normalize_medications(payload.medications)

    # Build snapshot document
    snapshot = {
        "patient_id": patient_id,
        "source": payload.source.value,
        "timestamp": datetime.now(timezone.utc),
        "medications": normalized_meds,
    }

    # Persist snapshot (append-only — we never update existing snapshots)
    snapshot_id = await db.insert_snapshot(snapshot)

    # Fetch all snapshots (including the one we just inserted) for conflict analysis
    all_snapshots = await db.get_snapshots_for_patient(patient_id)

    # Detect conflicts against other sources
    conflicts = detect_conflicts(
        patient_id=patient_id,
        incoming_source=payload.source.value,
        incoming_meds=normalized_meds,
        all_snapshots=all_snapshots,
    )

    # Persist conflicts
    inserted_count = await db.insert_many_conflicts(conflicts)

    return IngestMedicationsResponse(
        snapshot_id=snapshot_id,
        conflicts_detected=inserted_count,
        message=(
            f"Snapshot stored. {inserted_count} conflict(s) detected."
            if inserted_count
            else "Snapshot stored. No conflicts detected."
        ),
    )


# ---------------------------------------------------------------------------
# Conflict resolution service
# ---------------------------------------------------------------------------

async def resolve_conflict_by_id(
    conflict_id: str,
    resolution: ConflictResolution,
) -> dict:
    conflict = await db.get_conflict(conflict_id)
    if conflict is None:
        raise HTTPException(status_code=404, detail="Conflict not found")
    if conflict["status"] == "resolved":
        raise HTTPException(status_code=409, detail="Conflict is already resolved")

    ok = await db.resolve_conflict(conflict_id, resolution.model_dump(exclude_none=True))
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to resolve conflict")

    return {"conflict_id": conflict_id, "status": "resolved"}


# ---------------------------------------------------------------------------
# Reporting service
# ---------------------------------------------------------------------------

async def get_clinic_patients_with_conflicts(clinic_id: str) -> ClinicConflictsResponse:
    """
    Return all patients in a clinic that have at least 1 unresolved conflict.
    Uses a two-step approach: fetch patients, then aggregate conflict counts.
    This avoids a complex multi-collection join and keeps queries readable.
    """
    patients = await db.get_patient_by_clinic(clinic_id)
    if not patients:
        return ClinicConflictsResponse(
            clinic_id=clinic_id,
            patients_with_unresolved_conflicts=[],
            total=0,
        )

    patient_ids = [str(p["_id"]) for p in patients]

    # Single aggregation call to count unresolved conflicts per patient
    conflict_counts = await db.count_unresolved_conflicts_by_patient(patient_ids)

    summaries: list[PatientConflictSummary] = []
    for patient in patients:
        pid = str(patient["_id"])
        count = conflict_counts.get(pid, 0)
        if count >= 1:
            summaries.append(
                PatientConflictSummary(
                    patient_id=pid,
                    patient_name=patient["name"],
                    clinic_id=patient["clinic_id"],
                    unresolved_conflict_count=count,
                )
            )

    return ClinicConflictsResponse(
        clinic_id=clinic_id,
        patients_with_unresolved_conflicts=summaries,
        total=len(summaries),
    )
