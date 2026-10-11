# gcp-traditional-arch

A minimal 2-tier test project: **frontend** + **backend**, deployable to
**either GCP or AWS** with the same application code, behind a single HTTP
(no SSL) Load Balancer in a custom VPC, with a cloud storage bucket for data.
Built to be spun up, tested for a couple of hours, and torn down.

| Cloud | Cost breakdown | Deployment guide |
|---|---|---|
| GCP (Cloud Run) | `COST_BREAKDOWN.txt` | `GCP_DEPLOYMENT_GUIDE.md` |
| AWS (ECS Fargate) | `AWS_COST_BREAKDOWN.txt` | `AWS_DEPLOYMENT_GUIDE.md` |

Both guides are comprehensive, concept-by-concept walkthroughs (including
teardown steps) written for someone with no prior experience on that cloud.

## Services

- `frontend/` - simple page: submit a name, see the list of past visits
- `backend/` - FastAPI service: stores/returns visits via a pluggable storage
  backend (`backend/storage.py`) that auto-selects GCS, S3, or an in-memory
  fallback depending on which environment variable is set - same code, either
  cloud, no changes needed to switch.

## Local testing (Docker)

```
cd backend
docker build -t traditional-arch-backend .
docker run -p 8081:8080 --name backend-test traditional-arch-backend

cd ../frontend
docker build -t traditional-arch-frontend .
docker run -p 3000:3000 -e BACKEND_URL=http://host.docker.internal:8081 --name frontend-test traditional-arch-frontend
```

Open http://localhost:3000 - submit a name, confirm it shows up in the list.
With no `GCS_BUCKET_NAME` set, the backend automatically falls back to an
in-memory list so this works fully offline, with no GCP credentials needed.

> If port 3000 is already taken locally by something else, map to a free host
> port instead - e.g. `-p 3002:3000` - and just open that port instead. The
> container always listens on 3000 internally regardless of which host port
> you map it to; only the internal port (3000) matters once this is deployed
> to Cloud Run.

## Deployment

- **GCP**: see `GCP_DEPLOYMENT_GUIDE.md` - custom VPC, Cloud Storage bucket,
  Cloud Run (continuous deployment from this GitHub repo via Cloud Build),
  Serverless NEGs, backend services, URL map, and the HTTP load balancer.
- **AWS**: see `AWS_DEPLOYMENT_GUIDE.md` - custom VPC, S3 bucket, ECR, ECS on
  Fargate, Application Load Balancer with path-based routing, and GitHub
  Actions for CI/CD (workflows already in `.github/workflows/`, task
  definition templates in `aws/`).

Both include a full glossary of that cloud's concepts, troubleshooting tips,
and how to tear everything down afterward.
