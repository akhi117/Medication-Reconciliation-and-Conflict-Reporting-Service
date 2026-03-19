# app/config.py
# Central configuration — reads from environment variables (or .env via dotenv)

import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME: str = os.getenv("DB_NAME", "medication_db")

# Collection names kept here so they're easy to change without hunting through code
COLLECTION_PATIENTS = "patients"
COLLECTION_SNAPSHOTS = "medication_snapshots"
COLLECTION_CONFLICTS = "conflicts"
