# Medication Reconciliation & Conflict Reporting Service

## Overview

This project is a backend service built using FastAPI and MongoDB to handle medication reconciliation for patients across multiple sources such as clinic EMR, hospital discharge summaries, and patient-reported data.

The system ingests medication lists, normalizes them, detects conflicts between sources, and provides endpoints to view and resolve these conflicts.



## Approach

The goal was not just to build endpoints but to structure the system in a way that is easy to understand and extend.

I focused on:

* separating concerns (routes, DB, logic)
* keeping data models simple but extensible
* ensuring each request flow is traceable



## Architecture

### High-Level Flow

Client → FastAPI Routes → Service / Logic Layer → MongoDB



### Components

**1. Routes (API Layer)**

* Handles HTTP requests
* Performs validation
* Calls business logic
* Files:

  * `patients.py`
  * `medications.py`
  * `conflicts.py`
  * `reporting.py`



**2. Service / Logic Layer**

* Core business logic
* Conflict detection rules
* Normalization logic
* File:

  * `conflict_detection.py`



**3. Database Layer**

* MongoDB client and configuration
* Centralized DB access
* File:

  * `db.py`



**4. Data Model (Collections)**

* `patients`
* `medication_snapshots`
* `conflicts`

Snapshots are used instead of overwriting data to maintain history.



## Data Modeling Decisions

* **Snapshots instead of updates**
  Each ingestion creates a new snapshot to preserve history and allow tracking changes over time.

* **Separate conflict collection**
  Conflicts are stored independently for better querying and auditability.

* **Loose schema**
  MongoDB is used to allow flexibility in medication structure and future extensions.



## Conflict Detection

The system detects:

* Same drug with different dose
* Same drug with different status (active vs stopped)
* Drug class conflicts (based on predefined rules)

Conflicts are stored with:

* patient_id
* type
* involved sources
* resolution status



## Conflict Resolution

Conflicts can be resolved by:

* selecting a source of truth
* providing a reason

The system stores:

* chosen source
* reason
* timestamp



## Robustness

Handled cases:

* Missing fields → 422 validation errors
* Invalid status/source → rejected
* Unknown patient → 404
* Empty medication list → rejected

Tests cover these edge cases.



## Running the Project

### 1. Setup

```bash
pip install -r requirements.txt
```

### 2. Start MongoDB

Make sure MongoDB is running locally.



### 3. Run API

```bash
uvicorn app.main:app --reload
```



### 4. Swagger UI

```
http://localhost:8000/docs
```


### 5. Run Tests

```bash
pytest -v
```


### 6. Seed Data

```bash
python scripts/seed.py
```



## Testing

Tests include:

* conflict detection edge cases
* API validation scenarios
* reporting endpoint

All tests pass locally.



## Trade-offs & Limitations

* Conflict rules are static (JSON-based), not dynamic
* No authentication/authorization
* No pagination for large datasets
* Limited aggregation (only one reporting endpoint implemented)



## What I Would Do Next

If I had more time:

* add authentication (JWT)
* improve conflict rules engine
* add pagination and filtering
* optimize queries with indexing
* add background processing for large ingestions



## Time Spent

~8–10 hours

Focused on completing core requirements and ensuring correctness over adding extra features.


## AI Usage

AI tools were used for:

* initial scaffolding
* debugging environment issues
* understanding async behavior

All core logic and design decisions were reviewed and validated manually.

---


