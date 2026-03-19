# app/conflict_detection.py
# Core conflict detection engine.
#
# Design decisions:
# - Works on normalized medication dicts (name/dose/status all lowercased).
# - Only flags conflicts between DIFFERENT sources. Same-source updates are ignored.
# - Missing dose: skip dose comparison (cannot conclude mismatch from absence).
# - Drug present in one source but absent in another: NOT a conflict per spec.
# - Returns a list of conflict dicts ready to be inserted into MongoDB.

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.utils import get_drug_class, load_conflict_rules


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def detect_conflicts(
    patient_id: str,
    incoming_source: str,
    incoming_meds: list[dict],
    all_snapshots: list[dict],
) -> list[dict]:
    """
    Compare the incoming snapshot against all *other* source snapshots.
    Returns a (possibly empty) list of conflict documents ready for DB insert.
    """
    # Build a view: source -> latest medications for that source.
    # We only compare the most recent snapshot per source to avoid
    # flagging historical disagreements that have since been corrected.
    latest_by_source = _latest_meds_by_source(all_snapshots)

    # Remove the incoming source — we don't conflict against ourselves
    latest_by_source.pop(incoming_source, None)

    if not latest_by_source:
        # No other sources yet → nothing to compare
        return []

    # Index incoming meds by drug name for O(1) lookup
    incoming_index: dict[str, dict] = {m["name"]: m for m in incoming_meds}

    conflicts: list[dict] = []

    for other_source, other_meds in latest_by_source.items():
        other_index: dict[str, dict] = {m["name"]: m for m in other_meds}

        # Find drugs present in BOTH sources
        shared_drugs = set(incoming_index.keys()) & set(other_index.keys())

        for drug in shared_drugs:
            inc_med = incoming_index[drug]
            oth_med = other_index[drug]

            new_conflicts = _compare_two_meds(
                patient_id=patient_id,
                drug=drug,
                source_a=incoming_source,
                med_a=inc_med,
                source_b=other_source,
                med_b=oth_med,
            )
            conflicts.extend(new_conflicts)

    # Check unsafe combinations across the combined active medication list
    combined_active = _get_combined_active_meds(incoming_meds, latest_by_source)
    class_conflicts = _check_class_conflicts(patient_id, combined_active)
    conflicts.extend(class_conflicts)

    return conflicts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _latest_meds_by_source(snapshots: list[dict]) -> dict[str, list[dict]]:
    """
    For each source, pick the most recent snapshot and return its medications.
    Snapshots are assumed to be sorted oldest-first (as returned from DB).
    """
    latest: dict[str, dict] = {}
    for snap in snapshots:
        source = snap["source"]
        # Since snapshots are sorted ascending, later entries overwrite earlier
        latest[source] = snap
    return {src: snap["medications"] for src, snap in latest.items()}


def _compare_two_meds(
    patient_id: str,
    drug: str,
    source_a: str,
    med_a: dict,
    source_b: str,
    med_b: dict,
) -> list[dict]:
    """
    Compare a single drug between two sources.
    Returns 0, 1, or 2 conflict documents.
    """
    found: list[dict] = []

    # --- Status conflict ---
    if med_a["status"] != med_b["status"]:
        found.append(
            _build_conflict(
                patient_id=patient_id,
                drug=drug,
                conflict_type="status_conflict",
                details={
                    source_a: {"status": med_a["status"]},
                    source_b: {"status": med_b["status"]},
                },
            )
        )

    # --- Dose mismatch ---
    # Only flag when BOTH sources provide a dose value and they differ.
    dose_a = med_a.get("dose")
    dose_b = med_b.get("dose")
    if dose_a is not None and dose_b is not None and dose_a != dose_b:
        found.append(
            _build_conflict(
                patient_id=patient_id,
                drug=drug,
                conflict_type="dose_mismatch",
                details={
                    source_a: {"dose": dose_a},
                    source_b: {"dose": dose_b},
                },
            )
        )

    return found


def _get_combined_active_meds(
    incoming_meds: list[dict],
    other_sources: dict[str, list[dict]],
) -> list[str]:
    """
    Collect all unique drug names that are 'active' across the incoming
    snapshot and the latest snapshots of other sources.
    Used to detect unsafe combinations.
    """
    active_drugs: set[str] = set()

    for med in incoming_meds:
        if med["status"] == "active":
            active_drugs.add(med["name"])

    for meds in other_sources.values():
        for med in meds:
            if med["status"] == "active":
                active_drugs.add(med["name"])

    return list(active_drugs)


def _check_class_conflicts(patient_id: str, active_drugs: list[str]) -> list[dict]:
    """
    Check all active drugs against the unsafe_combinations rules.
    Returns a conflict for each unsafe pair found.
    """
    rules = load_conflict_rules()
    unsafe_combinations: list[dict] = rules.get("unsafe_combinations", [])

    # Build a class -> drugs mapping from the active list for efficient lookup
    active_set = set(active_drugs)
    drug_to_class = {
        drug: get_drug_class(drug) for drug in active_drugs if get_drug_class(drug)
    }
    # Reverse: class -> set of drugs in that class that are active
    class_to_drugs: dict[str, set[str]] = {}
    for drug, cls in drug_to_class.items():
        class_to_drugs.setdefault(cls, set()).add(drug)

    found: list[dict] = []

    for combo in unsafe_combinations:
        pair = combo["drugs"]  # e.g. ["warfarin", "aspirin"] or ["ssri", "maoi"]
        if len(pair) != 2:
            continue

        left, right = pair[0], pair[1]

        # Resolve each side: could be a drug name or a class name
        left_drugs = _resolve_to_drugs(left, active_set, class_to_drugs)
        right_drugs = _resolve_to_drugs(right, active_set, class_to_drugs)

        if left_drugs and right_drugs:
            drug_label = f"{'+'.join(sorted(left_drugs))}/{'+'.join(sorted(right_drugs))}"
            found.append(
                _build_conflict(
                    patient_id=patient_id,
                    drug=drug_label,
                    conflict_type="class_conflict",
                    details={
                        "reason": combo.get("reason", "Unsafe combination"),
                        "drugs_involved": {
                            pair[0]: list(left_drugs),
                            pair[1]: list(right_drugs),
                        },
                    },
                )
            )

    return found


def _resolve_to_drugs(
    name: str,
    active_set: set[str],
    class_to_drugs: dict[str, set[str]],
) -> set[str]:
    """
    Given either a drug name or a drug class name, return the set of
    matching active drugs.
    """
    if name in active_set:
        return {name}
    # Try treating it as a class
    return class_to_drugs.get(name, set())


def _build_conflict(
    patient_id: str,
    drug: str,
    conflict_type: str,
    details: dict,
) -> dict:
    return {
        "patient_id": patient_id,
        "drug": drug,
        "type": conflict_type,
        "details": details,
        "status": "unresolved",
        "resolution": None,
        "created_at": datetime.now(timezone.utc),
    }
