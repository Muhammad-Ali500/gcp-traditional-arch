# gcp-traditional-arch

A minimal 2-tier test project: **frontend** + **backend**, both on Cloud Run,
behind a single HTTP (no SSL) External Application Load Balancer in a custom
VPC, with a Cloud Storage bucket for data. Built to be spun up, tested for a
couple of hours, and torn down - see `COST_BREAKDOWN.txt` for the full cost
estimate and `GCP_DEPLOYMENT_GUIDE.md` for the comprehensive, concept-by-concept
Console setup walkthrough (including teardown steps).

## Services

- `frontend/` - simple page: submit a name, see the list of past visits
- `backend/` - FastAPI service: stores/returns visits via a Cloud Storage bucket

## Local testing (Docker)

```
cd backend
docker build -t traditional-arch-backend .
docker run -p 8081:8080 --name backend-test traditional-arch-backend

cd ../frontend
docker build -t traditional-arch-frontend .
docker run -p 8080:8080 -e BACKEND_URL=http://host.docker.internal:8081 --name frontend-test traditional-arch-frontend
```

Open http://localhost:8080 - submit a name, confirm it shows up in the list.
With no `GCS_BUCKET_NAME` set, the backend automatically falls back to an
in-memory list so this works fully offline, with no GCP credentials needed.

## Deployment

See `GCP_DEPLOYMENT_GUIDE.md` in this folder for the full GCP Console
walkthrough - custom VPC, Cloud Storage bucket, Cloud Run (continuous
deployment from this GitHub repo), Serverless NEGs, backend services, URL
map, and the HTTP load balancer, plus a glossary of every GCP concept used,
troubleshooting tips, and how to tear it all down afterward.
