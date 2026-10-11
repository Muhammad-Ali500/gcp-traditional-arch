"""
Backend API - traditional 2-tier architecture test project.

Stores "visit" records as small JSON objects in a Cloud Storage bucket, and
lists them back. Falls back to an in-memory list automatically if GCS isn't
reachable (no bucket configured yet, or no credentials) - this lets the
service run and be fully tested locally in Docker before any real GCP
resources exist, and deployed to Cloud Run.
"""
import datetime
import json
import logging
import os
import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backend")

app = FastAPI(title="Traditional Arch - Backend API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "")

_storage_client = None
_bucket = None
_gcs_available = False
_memory_store = []  # local fallback used whenever GCS isn't reachable

if BUCKET_NAME:
    try:
        from google.cloud import storage

        _storage_client = storage.Client()
        _bucket = _storage_client.bucket(BUCKET_NAME)
        # cheap call to confirm the bucket is actually reachable right now
        _bucket.exists()
        _gcs_available = True
        logger.info("Connected to GCS bucket '%s'", BUCKET_NAME)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "GCS bucket '%s' not reachable (%s) - falling back to in-memory storage.",
            BUCKET_NAME, exc,
        )
else:
    logger.info("GCS_BUCKET_NAME not set - running with in-memory storage only.")


class VisitBody(BaseModel):
    name: str


@app.get("/api/health")
def health():
    return {"status": "ok", "storage_backend": "gcs" if _gcs_available else "in-memory"}


@app.post("/api/visit")
def record_visit(body: VisitBody):
    visit_id = str(uuid.uuid4())[:8]
    record = {
        "id": visit_id,
        "name": body.name,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }

    if _gcs_available:
        blob = _bucket.blob(f"visits/{visit_id}.json")
        blob.upload_from_string(json.dumps(record), content_type="application/json")
    else:
        _memory_store.append(record)

    return {"ok": True, "visit": record}


@app.get("/api/visits")
def list_visits():
    if _gcs_available:
        blobs = _bucket.list_blobs(prefix="visits/")
        records = []
        for blob in blobs:
            try:
                records.append(json.loads(blob.download_as_text()))
            except Exception:  # noqa: BLE001
                continue
        records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return records[:50]

    return list(reversed(_memory_store[-50:]))
