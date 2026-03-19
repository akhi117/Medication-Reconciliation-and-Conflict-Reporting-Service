# app/models.py
# Pydantic models serve as the data contract for API input/output and internal use.
# We keep request models, response models, and DB document shapes all here for clarity.

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class MedicationSource(str, Enum):
    clinic_emr = "clinic_emr"
    hospital_discharge = "hospital_discharge"
    patient_reported = "patient_reported"


class MedicationStatus(str, Enum):
    active = "active"
    stopped = "stopped"


class ConflictType(str, Enum):
    dose_mismatch = "dose_mismatch"
    status_conflict = "status_conflict"
    class_conflict = "class_conflict"


class ConflictStatus(str, Enum):
    unresolved = "unresolved"
    resolved = "resolved"


# ---------------------------------------------------------------------------
# Medication item (used inside a snapshot)
# ---------------------------------------------------------------------------

class MedicationItem(BaseModel):
    name: str = Field(..., min_length=1, description="Drug name")
    dose: Optional[str] = Field(None, description="Dose string, e.g. '500mg'")
    status: MedicationStatus

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Medication name must not be blank")
        return stripped


# ---------------------------------------------------------------------------
# Ingestion request / response
# ---------------------------------------------------------------------------

class IngestMedicationsRequest(BaseModel):
    source: MedicationSource
    medications: list[MedicationItem] = Field(..., min_length=1)


class IngestMedicationsResponse(BaseModel):
    snapshot_id: str
    conflicts_detected: int
    message: str


# ---------------------------------------------------------------------------
# Conflict resolution payload (for PATCH endpoint)
# ---------------------------------------------------------------------------

class ConflictResolution(BaseModel):
    chosen_source: Optional[MedicationSource] = None
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Reporting response shapes
# ---------------------------------------------------------------------------

class PatientConflictSummary(BaseModel):
    patient_id: str
    patient_name: str
    clinic_id: str
    unresolved_conflict_count: int


class ClinicConflictsResponse(BaseModel):
    clinic_id: str
    patients_with_unresolved_conflicts: list[PatientConflictSummary]
    total: int
