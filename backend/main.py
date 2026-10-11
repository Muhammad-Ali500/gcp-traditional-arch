"""
Backend API - traditional 2-tier architecture test project.

Stores "visit" records as small JSON objects and lists them back. Storage
backend (GCS, S3, or in-memory) is selected automatically by storage.py
based on which environment variable is set - this same code deploys
unchanged to GCP (Cloud Run + Cloud Storage) or AWS (ECS/Fargate + S3).
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backend")

app = FastAPI(title="Traditional Arch - Backend API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class VisitBody(BaseModel):
    name: str


@app.get("/api/health")
def health():
    return {"status": "ok", "storage_backend": storage.backend_name()}


@app.post("/api/visit")
def record_visit(body: VisitBody):
    record = storage.record_visit(body.name)
    return {"ok": True, "visit": record}


@app.get("/api/visits")
def list_visits():
    return storage.list_visits()
