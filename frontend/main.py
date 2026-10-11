"""
Frontend - traditional 2-tier architecture test project.

Simple server-rendered page: submits a name to the backend (which stores it
in Cloud Storage) and displays the list of past visits. Calls the backend
server-side, so no CORS/browser-to-backend complexity for a GCP beginner.
"""
import os

import requests
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Traditional Arch - Frontend")
templates = Jinja2Templates(directory="templates")

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8081")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    error = None
    visits = []
    try:
        resp = requests.get(f"{BACKEND_URL}/api/visits", timeout=5)
        resp.raise_for_status()
        visits = resp.json()
    except Exception as exc:  # noqa: BLE001
        error = f"Could not reach backend at {BACKEND_URL}: {exc}"
    return templates.TemplateResponse(
        "index.html", {"request": request, "visits": visits, "error": error, "backend_url": BACKEND_URL}
    )


@app.post("/visit")
def submit_visit(name: str = Form(...)):
    try:
        requests.post(f"{BACKEND_URL}/api/visit", json={"name": name}, timeout=5)
    except Exception:  # noqa: BLE001
        pass
    return RedirectResponse("/", status_code=303)
