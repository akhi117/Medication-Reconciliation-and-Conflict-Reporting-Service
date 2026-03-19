# app/utils.py
# Stateless helper functions: normalization and conflict-rule loading.
# Keeping these pure functions separate makes them trivially testable.

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from app.models import MedicationItem


# ---------------------------------------------------------------------------
# Conflict rules — loaded once at startup and cached
# ---------------------------------------------------------------------------

RULES_PATH = Path(__file__).parent.parent / "conflict_rules.json"


@lru_cache(maxsize=1)
def load_conflict_rules() -> dict:
    """Load the static conflict rules JSON file. Cached after first call."""
    with open(RULES_PATH, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def normalize_drug_name(name: str) -> str:
    """Lowercase and strip whitespace from drug name."""
    return name.strip().lower()


def normalize_dose(dose: str | None) -> str | None:
    """
    Normalize dose string: lowercase, collapse whitespace, remove space
    between number and unit (e.g. '500 MG' -> '500mg').
    Returns None if dose is None.
    """
    if dose is None:
        return None
    normalized = dose.strip().lower()
    # Collapse any space between digits and alphabetic unit
    normalized = re.sub(r"(\d)\s+([a-z])", r"\1\2", normalized)
    return normalized


def normalize_medications(medications: list[MedicationItem]) -> list[dict]:
    """
    Convert a list of MedicationItem into normalized plain dicts
    ready for storage and conflict detection.
    """
    result = []
    for med in medications:
        result.append(
            {
                "name": normalize_drug_name(med.name),
                "dose": normalize_dose(med.dose),
                "status": med.status.value,
            }
        )
    return result


# ---------------------------------------------------------------------------
# Drug-class lookup (used in class_conflict detection)
# ---------------------------------------------------------------------------

def get_drug_class(drug_name: str) -> str | None:
    """Return the drug class for a normalized drug name, or None."""
    rules = load_conflict_rules()
    return rules.get("drug_classes", {}).get(drug_name)
