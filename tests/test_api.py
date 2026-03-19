# tests/test_api.py
# Integration tests for the FastAPI routes.
#
# Windows/asyncio fix: pytest-asyncio on Windows (ProactorEventLoop) closes
# the loop after each test when using function scope. The fix is to use a
# single session-scoped event loop and scope all async fixtures accordingly.
#
# DB isolation fix: instead of monkeypatching DB_NAME (which doesn't work
# because Motor caches the client), we directly reference the test DB by name
# and wipe it before/after each test.

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.db import get_client
import asyncio
import pytest

@pytest.fixture(scope="session")
def event_loop():
    """Create a fresh event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

TEST_DB_NAME = "medication_db_test"


# ---------------------------------------------------------------------------
# DB cleanup — runs before and after each test
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def clean_test_db():
    """Wipe all test collections before each test."""
    client = get_client()
    test_db = client[TEST_DB_NAME]
    await test_db["patients"].delete_many({})
    await test_db["medication_snapshots"].delete_many({})
    await test_db["conflicts"].delete_many({})
    yield
    await test_db["patients"].delete_many({})
    await test_db["medication_snapshots"].delete_many({})
    await test_db["conflicts"].delete_many({})


# ---------------------------------------------------------------------------
# Patch the DB name used by all db.py functions to the test database
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def use_test_db(monkeypatch):
    import app.db as app_db
    import app.config as app_config
    monkeypatch.setattr(app_config, "DB_NAME", TEST_DB_NAME)
    monkeypatch.setattr(app_db, "DB_NAME", TEST_DB_NAME)


# ---------------------------------------------------------------------------
# HTTP client fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def patient_id(client):
    """Create a test patient and return its ID."""
    resp = await client.post(
        "/api/v1/patients",
        params={"name": "Test Patient", "clinic_id": "clinic_test"},
    )
    assert resp.status_code == 201
    return resp.json()["patient_id"]


# ---------------------------------------------------------------------------
# Ingestion tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_medications_success(client, patient_id):
    payload = {
        "source": "clinic_emr",
        "medications": [
            {"name": "Metformin", "dose": "500 MG", "status": "active"}
        ],
    }
    resp = await client.post(f"/api/v1/patients/{patient_id}/medications", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert "snapshot_id" in body
    assert body["conflicts_detected"] == 0


@pytest.mark.asyncio
async def test_ingest_detects_dose_mismatch(client, patient_id):
    await client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={
            "source": "clinic_emr",
            "medications": [{"name": "metformin", "dose": "500mg", "status": "active"}],
        },
    )
    resp = await client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={
            "source": "hospital_discharge",
            "medications": [{"name": "metformin", "dose": "1000mg", "status": "active"}],
        },
    )
    assert resp.status_code == 201
    assert resp.json()["conflicts_detected"] >= 1


@pytest.mark.asyncio
async def test_ingest_detects_status_conflict(client, patient_id):
    await client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={
            "source": "clinic_emr",
            "medications": [{"name": "lisinopril", "dose": "10mg", "status": "active"}],
        },
    )
    resp = await client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={
            "source": "hospital_discharge",
            "medications": [{"name": "lisinopril", "dose": "10mg", "status": "stopped"}],
        },
    )
    assert resp.json()["conflicts_detected"] >= 1


@pytest.mark.asyncio
async def test_ingest_missing_source_returns_422(client, patient_id):
    resp = await client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={"medications": [{"name": "metformin", "dose": "500mg", "status": "active"}]},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_invalid_status_returns_422(client, patient_id):
    resp = await client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={
            "source": "clinic_emr",
            "medications": [{"name": "metformin", "dose": "500mg", "status": "unknown_status"}],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_invalid_source_returns_422(client, patient_id):
    resp = await client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={
            "source": "fax_machine",
            "medications": [{"name": "metformin", "dose": "500mg", "status": "active"}],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_unknown_patient_returns_404(client):
    fake_id = "507f1f77bcf86cd799439011"
    resp = await client.post(
        f"/api/v1/patients/{fake_id}/medications",
        json={
            "source": "clinic_emr",
            "medications": [{"name": "metformin", "dose": "500mg", "status": "active"}],
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ingest_empty_medications_returns_422(client, patient_id):
    resp = await client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={"source": "clinic_emr", "medications": []},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Reporting endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clinic_conflicts_report_empty(client):
    resp = await client.get("/api/v1/clinics/nonexistent_clinic/patients/conflicts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["patients_with_unresolved_conflicts"] == []


@pytest.mark.asyncio
async def test_clinic_conflicts_report_shows_patient_with_conflict(client):
    create_resp = await client.post(
        "/api/v1/patients",
        params={"name": "Conflict Patient", "clinic_id": "clinic_abc"},
    )
    pid = create_resp.json()["patient_id"]

    await client.post(
        f"/api/v1/patients/{pid}/medications",
        json={
            "source": "clinic_emr",
            "medications": [{"name": "warfarin", "dose": "2mg", "status": "active"}],
        },
    )
    await client.post(
        f"/api/v1/patients/{pid}/medications",
        json={
            "source": "hospital_discharge",
            "medications": [{"name": "warfarin", "dose": "5mg", "status": "active"}],
        },
    )

    resp = await client.get("/api/v1/clinics/clinic_abc/patients/conflicts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(p["patient_id"] == pid for p in body["patients_with_unresolved_conflicts"])


@pytest.mark.asyncio
async def test_clinic_conflicts_report_excludes_clean_patient(client):
    create_resp = await client.post(
        "/api/v1/patients",
        params={"name": "Clean Patient", "clinic_id": "clinic_xyz"},
    )
    pid = create_resp.json()["patient_id"]

    await client.post(
        f"/api/v1/patients/{pid}/medications",
        json={
            "source": "clinic_emr",
            "medications": [{"name": "atorvastatin", "dose": "20mg", "status": "active"}],
        },
    )

    resp = await client.get("/api/v1/clinics/clinic_xyz/patients/conflicts")
    body = resp.json()
    assert body["total"] == 0


# ---------------------------------------------------------------------------
# Conflict listing and resolution tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_patient_conflicts(client, patient_id):
    await client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={
            "source": "clinic_emr",
            "medications": [{"name": "metformin", "dose": "500mg", "status": "active"}],
        },
    )
    await client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={
            "source": "hospital_discharge",
            "medications": [{"name": "metformin", "dose": "1000mg", "status": "active"}],
        },
    )

    resp = await client.get(f"/api/v1/patients/{patient_id}/conflicts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1


@pytest.mark.asyncio
async def test_resolve_conflict(client, patient_id):
    await client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={
            "source": "clinic_emr",
            "medications": [{"name": "metformin", "dose": "500mg", "status": "active"}],
        },
    )
    await client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={
            "source": "hospital_discharge",
            "medications": [{"name": "metformin", "dose": "1000mg", "status": "active"}],
        },
    )

    conflicts_resp = await client.get(f"/api/v1/patients/{patient_id}/conflicts")
    conflict_id = conflicts_resp.json()["conflicts"][0]["_id"]

    resolve_resp = await client.patch(
        f"/api/v1/conflicts/{conflict_id}/resolve",
        json={"chosen_source": "clinic_emr", "reason": "Clinic record is more recent"},
    )
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["status"] == "resolved"