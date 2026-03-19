# app/api/routes.py
"""
HTTP API layer with FastAPI route handlers.

This module defines all REST endpoints for the Medication Conflict Manager.
Design philosophy:
- Routes are thin HTTP handlers that validate input and shape responses
- All business logic is delegated to the service layer (app/service.py)
- Database operations go through the db layer for clean separation of concerns
- Errors are converted to appropriate HTTP status codes via HTTPException

Endpoints are organized by feature:
1. Patient Management: Create and retrieve patient records
2. Medication Ingestion: Upload medication lists and trigger conflict detection
3. Conflict Management: List and resolve detected conflicts
4. Reporting: Dashboard data for clinic-wide conflict overview
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

from app import db, service
from app.models import (
    ClinicConflictsResponse,
    ConflictResolution,
    IngestMedicationsRequest,
    IngestMedicationsResponse,
)

# Initialize the APIRouter instance that will be imported and included in the main FastAPI app
# All endpoints defined below are added to this router
router = APIRouter()


# ============================================================================
# Patient Management Endpoints
# ============================================================================
# CRUD operations for patient records. In production, this would likely be more
# comprehensive with update, delete, and list endpoints. For now, minimal for
# setup/testing convenience.


@router.post("/patients", status_code=201, tags=["Patients"])
async def create_patient(name: str, clinic_id: str):
    """
    Create a new patient record linked to a clinic.
    
    Clinical systems typically have their own patient management; this endpoint
    allows integrating patients into the medication conflict manager.
    
    Query Parameters:
        name: Patient's full name
        clinic_id: Identifier of the clinic managing this patient
    
    Returns:
        201 Created with the new patient's ID and details
    """
    patient_id = await db.create_patient(name=name, clinic_id=clinic_id)
    return {"patient_id": patient_id, "name": name, "clinic_id": clinic_id}


@router.get("/patients/{patient_id}", tags=["Patients"])
async def get_patient(patient_id: str = Path(..., description="MongoDB ObjectId")):
    """
    Retrieve a patient record by ID.
    
    Path Parameters:
        patient_id: MongoDB ObjectId of the patient
    
    Returns:
        200 OK with patient document on success
        404 Not Found if patient does not exist
    """
    patient = await db.get_patient(patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    # Convert MongoDB ObjectId to string for JSON serialization
    patient["_id"] = str(patient["_id"])
    return patient


# ---------------------------------------------------------------------------
# Medication ingestion
# ---------------------------------------------------------------------------

# ============================================================================
# Medication Ingestion Endpoints
# ============================================================================
# When a patient's medication list is updated from any source (pharmacy system,
# EHR, patient portal), we ingest it, create a snapshot, and run conflict detection.


@router.post(
    "/patients/{patient_id}/medications",
    response_model=IngestMedicationsResponse,
    status_code=201,
    tags=["Medications"],
)
async def ingest_medications(
    payload: IngestMedicationsRequest,
    patient_id: str = Path(..., description="MongoDB ObjectId of the patient"),
):
    """
    Ingest a medication list from an external source and detect conflicts.
    
    This endpoint is called whenever medication data arrives from any source
    (e.g., pharmacy system, electronic health record, patient self-report).
    
    Flow:
    1. Validate the request and patient exists
    2. Store the medications as a new snapshot with source attribution
    3. Compare against existing snapshots from other sources
    4. Automatically create conflict records for inconsistencies
    5. Return newly detected conflicts to the caller
    
    Path Parameters:
        patient_id: MongoDB ObjectId of the patient
    
    Request Body:
        IngestMedicationsRequest containing medications, source, and timestamp
    
    Returns:
        201 Created with new snapshot ID and any conflicts detected
    """
    return await service.ingest_medications(patient_id, payload)


# ============================================================================
# Conflict Management Endpoints
# ============================================================================
# Utilities for viewing and resolving detected medication conflicts.


@router.get("/patients/{patient_id}/conflicts", tags=["Conflicts"])
async def list_patient_conflicts(patient_id: str):
    """
    List all unresolved conflicts for a patient.
    
    Used by clinical staff to review medication discrepancies that need attention.
    Each conflict includes:
    - Which medications are in conflict
    - Which sources disagree
    - Timestamps and other contextual data
    
    Path Parameters:
        patient_id: MongoDB ObjectId of the patient
    
    Returns:
        200 OK with list of conflicts or empty list if none exist
        404 Not Found if patient does not exist
    """
    patient = await db.get_patient(patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    conflicts = await db.get_unresolved_conflicts_for_patient(patient_id)
    # Convert MongoDB ObjectIds to strings for JSON serialization
    for c in conflicts:
        c["_id"] = str(c["_id"])
    return {"patient_id": patient_id, "conflicts": conflicts, "total": len(conflicts)}



@router.patch("/conflicts/{conflict_id}/resolve", tags=["Conflicts"])
async def resolve_conflict(conflict_id: str, resolution: ConflictResolution):
    """
    Mark a conflict as resolved with the clinician's decision.
    
    After reviewing a medication conflict, a clinician submits their resolution
    decision (e.g., "use pharmacy source" or "custom choice with reason").
    This endpoint records that decision and marks the conflict as resolved.
    
    Path Parameters:
        conflict_id: MongoDB ObjectId of the conflict
    
    Request Body:
        ConflictResolution containing chosen source/medication and optional reason
    
    Returns:
        200 OK with confirmation of resolution
        404 Not Found if conflict does not exist
        400 Bad Request if resolution is invalid
    """
    return await service.resolve_conflict_by_id(conflict_id, resolution)


# ============================================================================
# Reporting Endpoints
# ============================================================================
# High-level dashboard data aggregating conflicts across clinics and patients.


@router.get(
    "/clinics/{clinic_id}/patients/conflicts",
    response_model=ClinicConflictsResponse,
    tags=["Reporting"],
)
async def clinic_patients_with_conflicts(clinic_id: str):
    """
    Get all patients in a clinic with unresolved medication conflicts.
    
    Perfect for clinic dashboards and workflow prioritization. Shows which
    patients need medication reconciliation and how many conflicts each has.
    
    Path Parameters:
        clinic_id: Identifier of the clinic
    
    Returns:
        200 OK with list of patients and their conflict counts
        Includes summary statistics for easy triage and prioritization
    """
    return await service.get_clinic_patients_with_conflicts(clinic_id)
