# gcp-traditional-arch — AWS Deployment Guide

The same 2-tier test app (frontend + backend), deployed on **AWS** instead of GCP:
**ECS Fargate** (instead of Cloud Run) behind an **Application Load Balancer**
(instead of Google's Global External LB) in a **custom VPC**, with an **S3
bucket** (instead of Cloud Storage) for data. Same application code — the
backend's storage layer (`storage.py`) auto-detects which cloud it's running
on and uses S3 here, GCS there, with no code change required either way.

Written the same way as `GCP_DEPLOYMENT_GUIDE.md` — concept-by-concept, for
someone with no prior AWS experience either.

---

## Table of contents

1. [What's different from the GCP version, and why](#1-whats-different-from-the-gcp-version-and-why)
2. [Glossary — every AWS term used in this guide](#2-glossary--every-aws-term-used-in-this-guide)
3. [Prerequisites](#3-prerequisites)
4. [Cost summary](#4-cost-summary)
5. [Where things stand already](#5-where-things-stand-already)
6. [Step 1 — Create the VPC, subnets, and security groups](#6-step-1--create-the-vpc-subnets-and-security-groups)
7. [Step 2 — Create the S3 bucket](#7-step-2--create-the-s3-bucket)
8. [Step 3 — Create the ECR repositories](#8-step-3--create-the-ecr-repositories)
9. [Step 4 — Create the IAM roles](#9-step-4--create-the-iam-roles)
10. [Step 5 — Push the first images and create the ECS cluster](#10-step-5--push-the-first-images-and-create-the-ecs-cluster)
11. [Step 6 — Register the task definitions](#11-step-6--register-the-task-definitions)
12. [Step 7 — Build the Application Load Balancer](#12-step-7--build-the-application-load-balancer)
13. [Step 8 — Create the ECS services](#13-step-8--create-the-ecs-services)
14. [Step 9 — Wire up GitHub Actions for CI/CD](#14-step-9--wire-up-github-actions-for-cicd)
15. [Step 10 — Test it end-to-end](#15-step-10--test-it-end-to-end)
16. [Step 11 — Troubleshooting](#16-step-11--troubleshooting)
17. [Step 12 — Teardown (delete everything)](#17-step-12--teardown-delete-everything)
18. [Appendix: how a request flows through this system](#18-appendix-how-a-request-flows-through-this-system)

---

## 1. What's different from the GCP version, and why

```
 Your browser
      |
      |  HTTP (port 80, no SSL/HTTPS)
      v
 +-------------------------------+
 |  Application Load Balancer    |     <- one public DNS name/IP, one entry point
 |  (Listener rules: path-based) |
 +----------------+--------------+
       |                    |
   /*  (default)        /api/*
       |                    |
       v                    v
 +-----------+        +-----------+
 |  Target   |        |  Target   |
 |  Group    |        |  Group    |
 | (frontend)|        | (backend) |
 +-----+-----+        +-----+-----+
       |                    |
       v                    v
 +-----------+        +-----------+
 |   ECS     |        |   ECS     |
 |  Fargate  | -----> |  Fargate  |
 |  task     | (calls)|  task     |
 | (frontend)|        | (backend) |
 +-----------+        +-----+-----+
                             |
                             v
                      +-------------+
                      |  S3 bucket  |
                      +-------------+

 (Both Fargate tasks run directly in PUBLIC subnets with public IPs -
  no NAT Gateway needed, same cost-avoidance idea as skipping Cloud NAT.)
```

| GCP concept | AWS equivalent | Key difference to know |
|---|---|---|
| Cloud Run | **ECS on Fargate** | **No true scale-to-zero.** A Fargate task bills continuously per second while its desired count is ≥ 1, whether or not it's handling requests. This is the single biggest cost/behavior difference from Cloud Run. |
| Global External HTTP Load Balancer | **Application Load Balancer (ALB)** | Conceptually almost identical — same idea of listeners, routing rules, and backend targets, just AWS's naming. |
| Serverless NEG | **Target Group** (type: IP, pointing at the Fargate task's ENI) | AWS doesn't need a special "serverless" adapter — a Fargate task gets its own network interface directly, so a normal IP-based target group works. |
| Custom VPC | **VPC** (same concept, same reasons) | AWS requires the ALB's subnets to span **at least 2 Availability Zones** — GCP's version didn't have that minimum. |
| Cloud Storage bucket | **S3 bucket** | Same role, different API — handled by `storage.py`'s pluggable backend. |
| Artifact Registry | **ECR (Elastic Container Registry)** | Same role. |
| Cloud Build (auto-wired by Cloud Run) | **GitHub Actions** (you add the workflow yourself) | AWS has no single-click "continuously deploy from a repo" wizard built into ECS the way Cloud Run has — GitHub Actions is the standard, well-documented way to achieve the same push-to-deploy pipeline, and the workflow files are already written for you (`.github/workflows/`). |
| IAM (service account) | **IAM roles** (execution role + task role) | Conceptually the same idea — just two distinct AWS role types instead of one service account (see Glossary). |

---

## 2. Glossary — every AWS term used in this guide

**VPC (Virtual Private Cloud)** — same concept as GCP's VPC: your own private network. AWS VPCs are always "custom" by default (unlike GCP, which gives you an auto-mode default VPC) — there's no separate "custom vs default" choice to make here.

**Availability Zone (AZ)** — one of several physically separate data centers within an AWS region. An Application Load Balancer requires subnets in **at least 2 AZs** for redundancy, which is why this guide creates 2 public subnets instead of 1.

**Subnet** — same concept as GCP: a slice of IP address space within the VPC, tied to one AZ. A **public subnet** is one whose route table sends internet-bound traffic to an Internet Gateway, giving resources inside it (with a public IP assigned) direct internet access.

**Security Group** — a virtual firewall attached to a resource (here, the Fargate tasks and the ALB), controlling what inbound/outbound traffic is allowed. This is AWS's equivalent of GCP's VPC firewall rules, but attached per-resource rather than per-network.

**ECS (Elastic Container Service)** — AWS's container orchestration service. It doesn't run your containers itself — it schedules them onto compute, which here is...

**Fargate** — the serverless compute engine for ECS (and EKS). You give it a container image and a CPU/memory size, and Fargate runs it without you managing any EC2 instances. This is AWS's closest equivalent to Cloud Run, with the important exception of billing (see Section 1).

**Task definition** — a JSON blueprint describing a container: which image to run, how much CPU/memory, which ports, which environment variables, and which IAM roles it uses. This is roughly equivalent to a Cloud Run service's own configuration, but versioned explicitly — every change creates a new numbered "revision."

**Task** — one running instance of a task definition (one running container, in this design). Equivalent to a Cloud Run "instance."

**Service** — keeps a specified number of tasks running (e.g., "always keep 1 task running"), replacing any that crash, and connects them to a load balancer's target group. Roughly equivalent to a Cloud Run service's own autoscaling/health management, just explicitly configured rather than automatic.

**Cluster** — a logical grouping of services/tasks. With Fargate, a cluster doesn't represent real standing infrastructure (unlike the old EC2-backed ECS model) — it's just an organizational namespace.

**Application Load Balancer (ALB)** — AWS's Layer 7 (HTTP-aware) load balancer. Same role as GCP's Global External HTTP Load Balancer: one public entry point, routing by things like URL path.

**Target Group** — a named group of things the ALB can route to (here, each one wraps a Fargate service's tasks via their IP addresses). Equivalent to a GCP backend service.

**Listener** — the ALB's configuration for a specific port/protocol (here, HTTP on port 80) and the rules for what to do with matching requests.

**Listener rule** — the actual routing logic ("if path is `/api/*`, send to this target group; otherwise, send to that one"). Equivalent to GCP's URL map path rules.

**ECR (Elastic Container Registry)** — where your built container images are stored, equivalent to Artifact Registry. Unlike GCP, there's no automatic wizard that creates this for you — you create the repository yourself (Step 3).

**IAM role — Execution role vs. Task role (this trips up almost everyone new to ECS):**
- The **execution role** (`ecsTaskExecutionRole`) is used by ECS itself, behind the scenes, to pull your container image from ECR and write logs to CloudWatch. Every task needs one, and it's almost always the same standard AWS-managed role.
- The **task role** is used by *your application code* to call other AWS services — here, it's what lets the backend's code actually read/write the S3 bucket. This is the AWS equivalent of granting a Cloud Run service account a role on the bucket.

**CloudWatch Logs** — where your containers' stdout/stderr ends up, equivalent to Cloud Run's built-in Logs tab, except you need to reference a log group explicitly in the task definition (already set up in the provided JSON templates).

**GitHub Actions** — GitHub's own built-in CI/CD runner. On every push to `main`, it checks out your code, builds the Docker image, pushes it to ECR, and tells ECS to deploy it — mirroring what Cloud Build did automatically on the GCP side.

---

## 3. Prerequisites

- An AWS account with billing enabled.
- An **IAM user** (not your root account) with programmatic access (an access key ID + secret access key) and permissions for: ECS, ECR, IAM (to pass roles), EC2 (for VPC/ALB), S3, and CloudWatch Logs. For a quick test, attaching the AWS-managed `AdministratorAccess` policy to this user is the simplest option; for anything beyond a short-lived test, a scoped-down custom policy is the better practice.
- The code already pushed to GitHub: `https://github.com/Muhammad-Ali500/gcp-traditional-arch` (already done).
- AWS CLI installed locally is optional but handy for quick checks — this guide uses the Console throughout, matching the GCP guide's approach.

---

## 4. Cost summary

Full breakdown in `AWS_COST_BREAKDOWN.txt`. Short version:

| Resource | Cost for a 2-3 hour test |
|---|---|
| VPC + subnets + security groups | **$0.00** |
| ECS Fargate (2 tasks, smallest size) | **~$0.07** (bills continuously, unlike Cloud Run) |
| Application Load Balancer | **~$0.07-0.08** |
| S3 bucket | **~$0.00-0.01** |
| ECR (2 small images) | **~$0.00** |
| Data transfer out | **$0.00** (inside the always-free 100GB/month allowance) |
| **Total** | **~$0.15-0.20** |

If forgotten for a full month: **~$35/month** — meaningfully higher than GCP's ~$18.25/month figure, because Fargate has no scale-to-zero safety net. Prompt teardown matters more here than it did on GCP.

---

## 5. Where things stand already

- Application code is unchanged except for `backend/storage.py`, which now supports S3 in addition to GCS, selected automatically by which environment variable is set (`AWS_S3_BUCKET_NAME` vs `GCS_BUCKET_NAME`), falling back to in-memory storage if neither is reachable — tested locally in Docker for all three cases.
- GitHub Actions workflows are already written and committed: `.github/workflows/deploy-backend.yml` and `deploy-frontend.yml`.
- Task definition templates are already written: `aws/backend-task-def.json` and `aws/frontend-task-def.json` — you'll fill in a few placeholders (`<AWS_ACCOUNT_ID>`, `<REGION>`, `<YOUR_BUCKET_NAME>`) as you go.
- Nothing exists on AWS yet — this guide starts from zero.

> **No SSL/HTTPS, same as the GCP version** — you'll reach the app over plain HTTP via the ALB's DNS name, deliberately, to keep setup simple.

---

## 6. Step 1 — Create the VPC, subnets, and security groups

1. AWS Console → **VPC** → **Create VPC**.
2. Choose **"VPC and more"** (the wizard that creates subnets for you too) — this is the fastest path to a correctly-spread 2-AZ setup.
3. Name: `traditional-arch-vpc`. Number of Availability Zones: **2**. Number of public subnets: **2**. Number of private subnets: **0** (we don't need any — both tasks live in public subnets, deliberately, to avoid a NAT Gateway).
4. Leave NAT gateways set to **None** — this is the key setting that avoids the NAT Gateway cost entirely.
5. Click **Create VPC**.
6. Left menu → **Security Groups** → **Create security group**:
   - Name: `traditional-arch-alb-sg`. Inbound rule: HTTP (port 80) from `0.0.0.0/0` (anywhere). This is for the Load Balancer.
   - Create a second one: `traditional-arch-tasks-sg`. Inbound rules: port `8080` from `traditional-arch-alb-sg`, and port `3000` from `traditional-arch-alb-sg` (so only the ALB can reach the tasks directly, not the public internet).

---

## 7. Step 2 — Create the S3 bucket

1. Console → **S3** → **Create bucket**.
2. Name it something globally unique (bucket names are unique across *all* of AWS) — e.g. `traditional-arch-data-<yourname>`.
3. Region: match whatever region you used for the VPC (this guide assumes `us-east-1`).
4. Leave all other settings at their defaults (Block Public Access should stay **on** — nothing here needs to be public). Click **Create bucket**.

---

## 8. Step 3 — Create the ECR repositories

1. Console → **ECR** → **Create repository**.
2. Name: `traditional-arch-backend`. Leave settings at default. Create.
3. Repeat for `traditional-arch-frontend`.

These start empty — the GitHub Actions workflow will push the first real images in Step 9.

---

## 9. Step 4 — Create the IAM roles

1. **Execution role** (used by ECS to pull images + write logs — same for both services):
   - Console → **IAM → Roles → Create role**.
   - Trusted entity: **AWS service** → Use case: **Elastic Container Service** → **Elastic Container Service Task**.
   - Attach policy: `AmazonECSTaskExecutionRolePolicy` (AWS-managed).
   - Name it exactly: `ecsTaskExecutionRole` (this matches the ARN already written into both task definition templates).
2. **Task role** (used by the backend's own code to access S3):
   - IAM → Roles → Create role → Trusted entity: **AWS service** → **Elastic Container Service Task**.
   - Attach a policy granting S3 access to your specific bucket — easiest path: **Create policy** → JSON:
     ```json
     {
       "Version": "2012-10-17",
       "Statement": [{
         "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
         "Resource": [
           "arn:aws:s3:::YOUR-BUCKET-NAME",
           "arn:aws:s3:::YOUR-BUCKET-NAME/*"
         ]
       }]
     }
     ```
   - Name the role: `traditional-arch-backend-task-role` (matches the template).
3. Go back to `aws/backend-task-def.json` and `aws/frontend-task-def.json` in your local repo and replace `<AWS_ACCOUNT_ID>`, `<ACCOUNT_ID>`, `<REGION>`, and `<YOUR_BUCKET_NAME>` with your real values, then commit and push that change.

---

## 10. Step 5 — Push the first images and create the ECS cluster

1. Console → **ECS** → **Clusters** → **Create cluster**.
2. Name: `traditional-arch-cluster`. Infrastructure: **AWS Fargate (serverless)**. Create.
3. The ECR repositories from Step 3 are still empty — the very first image push happens automatically once GitHub Actions runs in Step 14, so for now, just leave the cluster created and move on; you'll come back to create the services in Step 8, after the ALB (Step 7) and the first successful GitHub Actions run have both happened.

---

## 11. Step 6 — Register the task definitions

1. Console → **ECS → Task definitions → Create new task definition** → **Create new task definition with JSON**.
2. Paste the contents of your locally-updated `aws/backend-task-def.json` (with real account ID/region/bucket filled in). The `image` field can stay as the placeholder for now — GitHub Actions will register a new revision with the real image tag on its first run.
3. Click **Create**.
4. Repeat for `aws/frontend-task-def.json`.

---

## 12. Step 7 — Build the Application Load Balancer

1. Console → **EC2 → Load Balancers → Create load balancer** → **Application Load Balancer**.
2. Name: `traditional-arch-alb`. Scheme: **Internet-facing**. IP address type: IPv4.
3. Network mapping: select `traditional-arch-vpc` and both public subnets from Step 1.
4. Security group: select `traditional-arch-alb-sg` (remove the default one if it's auto-added).
5. Listener: HTTP, port 80 (no HTTPS/certificate — same deliberate choice as the GCP version).
6. Under "Default action," create a new target group on the fly:
   - Target group name: `frontend-tg`. Target type: **IP**. Protocol/port: HTTP / 3000 (matches the frontend's container port). VPC: `traditional-arch-vpc`. Health check path: `/healthz`.
7. Click **Create load balancer**.
8. Go back into the load balancer → **Listeners** tab → edit the HTTP:80 listener → **Add rule**:
   - Condition: Path is `/api/*`.
   - Action: forward to a **new target group**: `backend-tg`, type **IP**, protocol/port HTTP / 8080, VPC `traditional-arch-vpc`, health check path `/api/health`.
   - Save.

---

## 13. Step 8 — Create the ECS services

1. Console → **ECS → Clusters → traditional-arch-cluster → Create service**.
2. Task definition: `traditional-arch-backend`, latest revision. Service name: `backend`. Desired tasks: **1**.
3. Networking: select `traditional-arch-vpc`, both public subnets, security group `traditional-arch-tasks-sg`. **Turn on "Public IP"** (this is the setting that replaces needing a NAT Gateway).
4. Load balancing: Application Load Balancer → select `traditional-arch-alb` → container to load balance: `backend:8080` → existing target group: `backend-tg`.
5. Create. Wait for the service to show 1/1 running tasks.
6. Repeat for the frontend: task definition `traditional-arch-frontend`, service name `frontend`, same networking (public IP on), load balancer target group `frontend-tg`.

> **Important — set the frontend's `BACKEND_URL` to the ALB's DNS name.** Since both services sit behind the same ALB with path-based routing, the frontend should call the backend via `http://<ALB-DNS-NAME>/api/visits`, not the backend task's own address (ECS tasks don't have a stable address the way a Cloud Run URL does). Update the `BACKEND_URL` value in `aws/frontend-task-def.json` to `http://<your-alb-dns-name>` (found on the Load Balancer's overview page), commit, push, and let GitHub Actions redeploy — this is the AWS equivalent of the GCP "point BACKEND_URL at the Load Balancer" fix from earlier in this project.

---

## 14. Step 9 — Wire up GitHub Actions for CI/CD

1. Create an IAM user dedicated to CI (separate from your own personal IAM user): **IAM → Users → Create user** → name `github-actions-deployer` → attach a policy allowing ECR push + ECS deploy (or `AdministratorAccess` for a quick test, same simplicity tradeoff as Step 3's prerequisite note).
2. Create an access key for that user: **Security credentials** tab → **Create access key** → choose **"Application running outside AWS"** → copy the Access Key ID and Secret Access Key (shown once).
3. In your GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**:
   - `AWS_ACCESS_KEY_ID`
   - `AWS_SECRET_ACCESS_KEY`
4. Push any change to `main` under `backend/` or `frontend/` — the matching workflow (already committed) will build, push to ECR, and deploy automatically. Check progress under your repo's **Actions** tab.

> For anything beyond a short test, AWS/GitHub's recommended best practice is **OIDC federation** instead of long-lived access keys (no secret to store or rotate at all) — worth learning next, but access keys are the simpler starting point for this exercise.

---

## 15. Step 10 — Test it end-to-end

1. Find the ALB's DNS name: **EC2 → Load Balancers → traditional-arch-alb** → copy the "DNS name" field.
2. Open `http://<that-dns-name>/` — should show the frontend.
3. Submit a name — confirm it appears in the list.
4. Open `http://<that-dns-name>/api/visits` directly — confirm it returns the same data as raw JSON.

---

## 16. Step 11 — Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| ALB DNS name times out | DNS propagation, or security group blocking port 80 | Wait a minute; double check `traditional-arch-alb-sg` allows inbound 80 from `0.0.0.0/0` |
| Target group shows targets as "unhealthy" | Health check path wrong, task not listening on the expected port, or security group blocking the ALB→task path | Confirm health check paths (`/healthz` for frontend, `/api/health` for backend) and that `traditional-arch-tasks-sg` allows inbound from `traditional-arch-alb-sg` on the right ports |
| Task stuck in "PROVISIONING" or keeps stopping | Execution role missing/misnamed, or image pull failing | ECS → cluster → service → Tasks tab → click the stopped task → check the "Stopped reason" |
| Frontend shows "Could not reach backend" | `BACKEND_URL` wrong, or backend's target group unhealthy | Confirm `BACKEND_URL` is the ALB's DNS name (not the backend task's own address), and that `/api/health` is passing its health check |
| GitHub Actions workflow fails at "Configure AWS credentials" | Secrets not set, or IAM user lacks permission | Repo Settings → Secrets → confirm both secrets exist and are spelled exactly right |
| GitHub Actions fails at ECR push | ECR repository doesn't exist yet, or IAM user lacks ECR permissions | Confirm Step 3 was completed and the repository name matches the workflow's `ECR_REPOSITORY` value exactly |

---

## 17. Step 12 — Teardown (delete everything)

> **⚠️ Important: unlike Cloud Run, both Fargate tasks keep billing continuously until you delete the services** — do this as soon as you're done testing, more urgently than on the GCP side.

Delete in this order:

1. **ECS → cluster → Services** → update both services' desired count to **0** first, then delete both services.
2. **ECS → Clusters** → delete `traditional-arch-cluster`.
3. **EC2 → Load Balancers** → delete `traditional-arch-alb`.
4. **EC2 → Target Groups** → delete `frontend-tg` and `backend-tg`.
5. **ECR → Repositories** → delete `traditional-arch-backend` and `traditional-arch-frontend` (this also deletes the stored images).
6. **S3 → Buckets** → empty and delete your bucket (this permanently deletes the test data, as expected).
7. **VPC → Your VPCs** → delete `traditional-arch-vpc` (this cleans up its subnets, route tables, and internet gateway together).
8. **IAM → Roles** → delete `traditional-arch-backend-task-role` and its custom policy (the `ecsTaskExecutionRole` is typically worth keeping, since it's a standard reusable role with no cost and no app-specific data in it).
9. **IAM → Users** → delete the `github-actions-deployer` user and its access key if you don't plan to use this pipeline again.
10. Double-check: **ECS** shows no running tasks/services, and **EC2 → Load Balancers** is empty — those were the two things actually costing you money continuously.

---

## 18. Appendix: how a request flows through this system

1. You open `http://<alb-dns-name>/` in your browser.
2. The ALB's HTTP:80 listener checks its rules: path `/` doesn't match `/api/*`, so it uses the default action → `frontend-tg`.
3. The ALB forwards the request to one of the frontend task's IP addresses (registered in `frontend-tg`).
4. The frontend task's FastAPI app renders the page — but first calls `BACKEND_URL/api/visits`, which is the **ALB's own DNS name** with `/api` — so that call goes back out to the ALB.
5. This time, the ALB's listener rule sees `/api/visits` matches `/api/*`, and forwards it to `backend-tg` instead.
6. The backend task reads records from S3 (using its task role's permissions) and returns them as JSON.
7. The frontend renders that into HTML and returns it — back through the ALB — to your browser.

Note the difference from the GCP version here: on GCP, the frontend called the backend's *own direct URL*, bypassing the Load Balancer entirely. On AWS, because ECS tasks don't have a stable, directly-callable address the way Cloud Run services do, the frontend calls the backend **through the same ALB** that serves public traffic — which is actually closer to the "properly locked down" pattern discussed for the GCP version, just arrived at by necessity rather than extra hardening.
