# tests/test_conflict_detection.py
# Unit tests for the conflict detection engine.
# These are pure function tests — no DB or HTTP involved.

import pytest

from app.conflict_detection import detect_conflicts


def _make_snapshot(source: str, meds: list[dict]) -> dict:
    """Helper to build a minimal snapshot dict."""
    return {"source": source, "medications": meds}


# ---------------------------------------------------------------------------
# Dose mismatch tests
# ---------------------------------------------------------------------------

def test_dose_mismatch_detected():
    """Two sources disagree on dose → conflict."""
    snapshots = [
        _make_snapshot("clinic_emr", [{"name": "metformin", "dose": "500mg", "status": "active"}]),
        _make_snapshot("hospital_discharge", [{"name": "metformin", "dose": "1000mg", "status": "active"}]),
    ]
    conflicts = detect_conflicts(
        patient_id="p1",
        incoming_source="hospital_discharge",
        incoming_meds=snapshots[1]["medications"],
        all_snapshots=snapshots,
    )
    dose_conflicts = [c for c in conflicts if c["type"] == "dose_mismatch"]
    assert len(dose_conflicts) == 1
    assert dose_conflicts[0]["drug"] == "metformin"


def test_no_conflict_when_doses_match():
    """Same dose across sources → no conflict."""
    snapshots = [
        _make_snapshot("clinic_emr", [{"name": "metformin", "dose": "500mg", "status": "active"}]),
        _make_snapshot("hospital_discharge", [{"name": "metformin", "dose": "500mg", "status": "active"}]),
    ]
    conflicts = detect_conflicts(
        patient_id="p1",
        incoming_source="hospital_discharge",
        incoming_meds=snapshots[1]["medications"],
        all_snapshots=snapshots,
    )
    assert not any(c["type"] == "dose_mismatch" for c in conflicts)


def test_missing_dose_skips_conflict():
    """If one source is missing a dose, skip dose conflict check."""
    snapshots = [
        _make_snapshot("clinic_emr", [{"name": "metformin", "dose": None, "status": "active"}]),
        _make_snapshot("hospital_discharge", [{"name": "metformin", "dose": "500mg", "status": "active"}]),
    ]
    conflicts = detect_conflicts(
        patient_id="p1",
        incoming_source="hospital_discharge",
        incoming_meds=snapshots[1]["medications"],
        all_snapshots=snapshots,
    )
    assert not any(c["type"] == "dose_mismatch" for c in conflicts)


# ---------------------------------------------------------------------------
# Status conflict tests
# ---------------------------------------------------------------------------

def test_status_conflict_detected():
    """One source says active, another says stopped → conflict."""
    snapshots = [
        _make_snapshot("clinic_emr", [{"name": "lisinopril", "dose": "10mg", "status": "active"}]),
        _make_snapshot("hospital_discharge", [{"name": "lisinopril", "dose": "10mg", "status": "stopped"}]),
    ]
    conflicts = detect_conflicts(
        patient_id="p1",
        incoming_source="hospital_discharge",
        incoming_meds=snapshots[1]["medications"],
        all_snapshots=snapshots,
    )
    status_conflicts = [c for c in conflicts if c["type"] == "status_conflict"]
    assert len(status_conflicts) == 1
    assert status_conflicts[0]["drug"] == "lisinopril"


def test_no_status_conflict_same_status():
    """Both sources say active → no status conflict."""
    snapshots = [
        _make_snapshot("clinic_emr", [{"name": "lisinopril", "dose": "10mg", "status": "active"}]),
        _make_snapshot("hospital_discharge", [{"name": "lisinopril", "dose": "10mg", "status": "active"}]),
    ]
    conflicts = detect_conflicts(
        patient_id="p1",
        incoming_source="hospital_discharge",
        incoming_meds=snapshots[1]["medications"],
        all_snapshots=snapshots,
    )
    assert not any(c["type"] == "status_conflict" for c in conflicts)


# ---------------------------------------------------------------------------
# Same-source update — should NOT conflict
# ---------------------------------------------------------------------------

def test_same_source_update_no_conflict():
    """
    Same source sends updated dose. The new snapshot replaces the old one
    as the 'latest' for that source. No cross-source comparison → no conflict.
    """
    snapshots = [
        _make_snapshot("clinic_emr", [{"name": "metformin", "dose": "500mg", "status": "active"}]),
        _make_snapshot("clinic_emr", [{"name": "metformin", "dose": "1000mg", "status": "active"}]),
    ]
    conflicts = detect_conflicts(
        patient_id="p1",
        incoming_source="clinic_emr",
        incoming_meds=snapshots[1]["medications"],
        all_snapshots=snapshots,
    )
    assert conflicts == []


# ---------------------------------------------------------------------------
# Drug absent in one source — should NOT conflict
# ---------------------------------------------------------------------------

def test_drug_absent_in_one_source_no_conflict():
    """Drug only present in one source is not a conflict per spec."""
    snapshots = [
        _make_snapshot("clinic_emr", [{"name": "metformin", "dose": "500mg", "status": "active"}]),
        _make_snapshot("hospital_discharge", [{"name": "lisinopril", "dose": "10mg", "status": "active"}]),
    ]
    conflicts = detect_conflicts(
        patient_id="p1",
        incoming_source="hospital_discharge",
        incoming_meds=snapshots[1]["medications"],
        all_snapshots=snapshots,
    )
    assert conflicts == []


# ---------------------------------------------------------------------------
# Class conflict (unsafe combination)
# ---------------------------------------------------------------------------

def test_class_conflict_warfarin_aspirin():
    """Warfarin + Aspirin from different sources → class_conflict."""
    snapshots = [
        _make_snapshot("clinic_emr", [{"name": "warfarin", "dose": "5mg", "status": "active"}]),
        _make_snapshot("patient_reported", [{"name": "aspirin", "dose": "81mg", "status": "active"}]),
    ]
    conflicts = detect_conflicts(
        patient_id="p1",
        incoming_source="patient_reported",
        incoming_meds=snapshots[1]["medications"],
        all_snapshots=snapshots,
    )
    class_conflicts = [c for c in conflicts if c["type"] == "class_conflict"]
    assert len(class_conflicts) == 1


def test_class_conflict_not_flagged_when_stopped():
    """Warfarin stopped + Aspirin active → no dangerous combination."""
    snapshots = [
        _make_snapshot("clinic_emr", [{"name": "warfarin", "dose": "5mg", "status": "stopped"}]),
        _make_snapshot("patient_reported", [{"name": "aspirin", "dose": "81mg", "status": "active"}]),
    ]
    conflicts = detect_conflicts(
        patient_id="p1",
        incoming_source="patient_reported",
        incoming_meds=snapshots[1]["medications"],
        all_snapshots=snapshots,
    )
    class_conflicts = [c for c in conflicts if c["type"] == "class_conflict"]
    assert len(class_conflicts) == 0


# ---------------------------------------------------------------------------
# No snapshots — first ever ingestion
# ---------------------------------------------------------------------------

def test_no_conflicts_on_first_ingestion():
    """First snapshot for a patient has nothing to compare against."""
    meds = [{"name": "metformin", "dose": "500mg", "status": "active"}]
    snapshot = _make_snapshot("clinic_emr", meds)
    conflicts = detect_conflicts(
        patient_id="p1",
        incoming_source="clinic_emr",
        incoming_meds=meds,
        all_snapshots=[snapshot],
    )
    assert conflicts == []


# ---------------------------------------------------------------------------
# Multiple conflicts in one ingestion
# ---------------------------------------------------------------------------

def test_multiple_conflicts_detected():
    """Two drugs each with a conflict → two separate conflict docs."""
    snapshots = [
        _make_snapshot("clinic_emr", [
            {"name": "metformin", "dose": "500mg", "status": "active"},
            {"name": "lisinopril", "dose": "10mg", "status": "active"},
        ]),
        _make_snapshot("hospital_discharge", [
            {"name": "metformin", "dose": "1000mg", "status": "active"},   # dose mismatch
            {"name": "lisinopril", "dose": "10mg", "status": "stopped"},   # status conflict
        ]),
    ]
    conflicts = detect_conflicts(
        patient_id="p1",
        incoming_source="hospital_discharge",
        incoming_meds=snapshots[1]["medications"],
        all_snapshots=snapshots,
    )
    assert len(conflicts) == 2
    types = {c["type"] for c in conflicts}
    assert "dose_mismatch" in types
    assert "status_conflict" in types
