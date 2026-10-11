"""
Frontend - traditional 2-tier architecture test project.

Simple server-rendered page: submits a name to the backend (which stores it
in Cloud Storage) and displays the list of past visits. Calls the backend
server-side, so no CORS/browser-to-backend complexity for a GCP beginner.

BACKEND_URL is fully externalized via environment variable - it can point at
the backend's direct Cloud Run URL (simple, default) or at a Load Balancer's
IP/domain (e.g. if the backend's ingress is locked to "Internal and Cloud
Load Balancing" - see GCP_DEPLOYMENT_GUIDE.md). No code change is needed to
switch between the two; only the deployed env var value changes.
"""
import logging
import os

import requests
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("frontend")

app = FastAPI(title="Traditional Arch - Frontend")
templates = Jinja2Templates(directory="templates")

# Strip any trailing slash so "{BACKEND_URL}/api/visits" never produces a
# double slash regardless of how the env var was set.
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8081").rstrip("/")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request, submit_failed: bool = False):
    error = None
    visits = []
    try:
        resp = requests.get(f"{BACKEND_URL}/api/visits", timeout=5)
        resp.raise_for_status()
        visits = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not reach backend at %s: %s", BACKEND_URL, exc)
        error = f"Could not reach backend at {BACKEND_URL}: {exc}"
    if submit_failed and not error:
        error = "Your submission could not be saved - the backend did not respond. Please try again."
    return templates.TemplateResponse(
        "index.html", {"request": request, "visits": visits, "error": error, "backend_url": BACKEND_URL}
    )


@app.post("/visit")
def submit_visit(name: str = Form(...)):
    try:
        resp = requests.post(f"{BACKEND_URL}/api/visit", json={"name": name}, timeout=5)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to submit visit to backend at %s: %s", BACKEND_URL, exc)
        return RedirectResponse("/?submit_failed=true", status_code=303)
    return RedirectResponse("/", status_code=303)
