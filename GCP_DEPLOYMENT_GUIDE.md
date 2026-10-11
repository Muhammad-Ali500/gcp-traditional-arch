# gcp-traditional-arch — Complete GCP Deployment Guide

A 2-tier web app (frontend + backend, both on Cloud Run) behind an HTTP-only
Load Balancer in a custom VPC, with a Cloud Storage bucket for data — built
and deployed from scratch, explained concept-by-concept for someone with
**zero prior GCP experience**.

By the end of this document you will have: created a GCP project, built a
custom network, stood up two serverless services wired to continuous
deployment from GitHub, connected them to a real Load Balancer, tested the
whole thing end-to-end, and torn it all back down — and understood *why*
each piece exists along the way, not just *what* to click.

---

## Table of contents

1. [What you're building, and why each piece exists](#1-what-youre-building-and-why-each-piece-exists)
2. [Glossary — every GCP term used in this guide](#2-glossary--every-gcp-term-used-in-this-guide)
3. [Prerequisites](#3-prerequisites)
4. [Cost summary](#4-cost-summary)
5. [Where things stand already](#5-where-things-stand-already)
6. [Step 1 — Create the GCP project and enable APIs](#6-step-1--create-the-gcp-project-and-enable-apis)
7. [Step 2 — Create the custom VPC and subnet](#7-step-2--create-the-custom-vpc-and-subnet)
8. [Step 3 — Create the Cloud Storage bucket](#8-step-3--create-the-cloud-storage-bucket)
9. [Step 4 — Deploy the backend to Cloud Run](#9-step-4--deploy-the-backend-to-cloud-run)
10. [Step 5 — Grant the backend access to the bucket](#10-step-5--grant-the-backend-access-to-the-bucket)
11. [Step 6 — Deploy the frontend to Cloud Run](#11-step-6--deploy-the-frontend-to-cloud-run)
12. [Step 7 — Build the HTTP Load Balancer](#12-step-7--build-the-http-load-balancer)
13. [Step 8 — Test it end-to-end](#13-step-8--test-it-end-to-end)
14. [Step 9 — Troubleshooting](#14-step-9--troubleshooting)
15. [Step 10 — Teardown (delete everything)](#15-step-10--teardown-delete-everything)
16. [Appendix: how a request actually flows through this system](#16-appendix-how-a-request-actually-flows-through-this-system)

---

## 1. What you're building, and why each piece exists

```
 Your browser
      |
      |  HTTP (port 80, no SSL/HTTPS)
      v
 +-------------------------------+
 |  Global External HTTP         |     <- one public IP, one entry point
 |  Load Balancer                |
 |  (URL Map: path-based routing)|
 +----------------+--------------+
       |                    |
   /*  (default)        /api/*
       |                    |
       v                    v
 +-----------+        +-----------+
 | Serverless|        | Serverless|
 | NEG       |        | NEG       |
 +-----+-----+        +-----+-----+
       |                    |
       v                    v
 +-----------+        +-----------+
 | Cloud Run |        | Cloud Run |
 | frontend  | -----> | backend   |
 +-----------+ (calls)+-----+-----+
                             |
                             v
                      +-------------+
                      |Cloud Storage|
                      |   bucket    |
                      +-------------+

 (Custom VPC wraps the networking layer; nothing above strictly
  needs to live "inside" it for this design to work — see the
  Glossary entry on Serverless NEGs for why.)
```

| Piece | Why it's here |
|---|---|
| **Frontend (Cloud Run)** | Serves the web page a human visits. Calls the backend server-side so there's no browser CORS complexity. |
| **Backend (Cloud Run)** | The API. Reads/writes small JSON records to Cloud Storage. This is the "data layer" of the app. |
| **Cloud Storage bucket** | Where the backend actually persists data — a real, durable store, not just memory that vanishes on restart. |
| **Load Balancer (HTTP only)** | Gives you ONE public entry point and ONE IP address, and routes `/api/*` to the backend and everything else to the frontend — this is what makes it look and behave like a real production app instead of two separate URLs. No SSL/HTTPS, per your explicit choice, to keep this test simple. |
| **Custom VPC** | A private network you control (vs. Google's shared default network) — the more "traditional" / production-realistic way to structure a GCP project, and what the Load Balancer's networking pieces attach to. |
| **Cloud Build + GitHub** | Every `git push` to `main` automatically rebuilds and redeploys the affected Cloud Run service — no manual deploy steps once this is wired up. |

This is intentionally a **test/learning build** — see [Step 10](#15-step-10--teardown-delete-everything) for tearing it down, since the only resource here that bills by the hour is the Load Balancer's forwarding rule.

---

## 2. Glossary — every GCP term used in this guide

**Project** — the top-level container for everything in GCP. Billing, APIs, and every resource all belong to exactly one project. You can delete a whole project to instantly delete everything inside it.

**VPC (Virtual Private Cloud)** — your own private network inside Google's infrastructure. Resources inside it can talk to each other privately; resources outside it can't, unless you explicitly allow it. The **default VPC** every new project comes with is fine for quick tests, but a **custom VPC** (what we build here) is the realistic, production-style choice — you control the IP ranges and subnets yourself instead of accepting Google's defaults.

**Subnet** — a slice of IP address space within a VPC, tied to one region. Resources get their private IP addresses from whichever subnet they're placed in.

**Cloud Run** — a serverless platform that runs a container (built from your Dockerfile) and automatically scales it up and down — including down to **zero** instances (and zero cost) when nobody's using it. You don't manage any servers; you just give it a container image and it handles the rest.

**Cloud Build** — Google's build service. When you tell a Cloud Run service to "continuously deploy from a repository," Cloud Build is the thing that actually watches your GitHub repo, builds a new container image from your Dockerfile every time you push, and hands that image to Cloud Run.

**Artifact Registry** — where the container images Cloud Build produces actually get stored. Created automatically the first time you set up continuous deployment; you never have to touch it directly for this project.

**Cloud Storage / bucket** — Google's object storage (like a limitless folder in the cloud). A "bucket" is a named container for files ("objects"). This is where the backend saves the small JSON records this app creates.

**Load Balancer (specifically: Global External Application Load Balancer)** — a Google-managed system that gives you one stable public IP address and distributes incoming requests to the right backend based on rules you define (like URL path). "Global" means the same IP works no matter which region a visitor connects from near them — Google's own edge network handles routing them to the closest healthy backend.

**Serverless NEG (Network Endpoint Group)** — the bridge that lets a Load Balancer send traffic to a Cloud Run service. Normally a Load Balancer points at VMs; a Serverless NEG is a special adapter that instead points at a Cloud Run (or Cloud Functions / App Engine) service directly — **without** that service needing to be connected to any VPC at all. This is why this design doesn't need a VPC connector or Cloud NAT: Serverless NEGs bypass that requirement entirely.

**Backend service** — a Load Balancer concept: a named group of backends (in our case, each one wraps a single Serverless NEG) that the Load Balancer can route traffic to, along with settings like health checks. We create two: one wrapping the frontend, one wrapping the backend.

**URL map** — the Load Balancer's routing table. It's the rule that says "requests to `/api/*` go to this backend service, everything else goes to that one."

**Target proxy** — sits between the URL map and the forwarding rule; translates incoming HTTP requests according to the URL map's rules. (Created automatically by the Console wizard — you won't configure this by hand.)

**Forwarding rule** — the actual object that owns the public IP address and port, and forwards matching traffic to the target proxy. **This is the one resource in this whole design that's billed by the hour** for as long as it exists, which is why deleting it is the priority during teardown.

**IAM (Identity and Access Management)** — Google's permission system. Every resource and every identity (user, or a service's own "service account") has roles granted to it that control what it's allowed to do. We use this once, in Step 5, to let the backend's service account write to the storage bucket.

**Service account** — an identity that belongs to a service (like a Cloud Run deployment) rather than a human. By default, Cloud Run uses the project's shared "Compute Engine default service account" unless you create and assign a dedicated one.

---

## 3. Prerequisites

- A Google account with a GCP **billing account** linked (a card on file — this test should cost under $0.20 total, see the cost summary below).
- The code already pushed to GitHub: `https://github.com/Muhammad-Ali500/gcp-traditional-arch` (already done — see [Section 5](#5-where-things-stand-already)).
- A modern browser, logged into the Google account you'll use for this project.
- No local tools required for the deployment itself — everything in this guide happens in the GCP Console at [console.cloud.google.com](https://console.cloud.google.com). (Local Docker testing was already done separately — see `README.md`.)

---

## 4. Cost summary

Full breakdown lives in `COST_BREAKDOWN.txt` in this same folder. The short version:

| Resource | Cost for a 2-3 hour test |
|---|---|
| Cloud Run (both services) | **$0.00** — well inside the free tier |
| Cloud Storage bucket | **~$0.00** — pennies even without the free tier |
| Custom VPC + subnet | **$0.00** — free to create and hold |
| Serverless NEGs | **$0.00** — no charge for the object itself |
| Cloud Build | **$0.00** — inside the 2,500 free build-minutes/month |
| Artifact Registry | **$0.00** — inside the 0.5 GB free tier |
| **Load Balancer forwarding rule** | **~$0.05–$0.08** (the only thing billed hourly: $0.025/hr) |
| **Total** | **~$0.08–$0.15** |

If you forget to tear it down, the forwarding rule alone would run ~$18.25/month if left for a full 30 days — everything else stays near $0 regardless of how long it sits idle. This is exactly why [Step 10](#15-step-10--teardown-delete-everything) exists and matters.

---

## 5. Where things stand already

The application code is **written, tested locally in Docker** (frontend → backend → storage flow fully verified working on this machine), and **already pushed** to GitHub:

```
https://github.com/Muhammad-Ali500/gcp-traditional-arch
```

Everything from here on is building the real GCP infrastructure through the Console and connecting it to that repo. Nothing in GCP exists yet — this guide starts from a completely empty project.

> **Important:** this design intentionally has **no SSL/HTTPS**. You will reach the app over plain HTTP using the Load Balancer's raw IP address. That's deliberate — it keeps setup simple and avoids certificate provisioning for a short-lived test. (For reference: Google-managed SSL certificates are actually free, so skipping SSL here is purely about simplicity, not about dodging a real cost.)

---

## 6. Step 1 — Create the GCP project and enable APIs

1. Go to **console.cloud.google.com** and sign in.
2. Click the project dropdown at the top of the page (next to the "Google Cloud" logo) → **New Project**.
3. Give it a name (e.g. `traditional-arch-test`). Leave organization as "No organization" if this is a personal account. Click **Create**.
4. Wait for the notification bell (top right) to confirm "Project created," then use the project dropdown again to **switch into it** — the entire Console scopes itself to whichever project is currently selected there, so always check this first if a resource you expect to see is "missing."
5. Left menu → **Billing** → confirm a billing account is linked to this project.
6. Left menu → **APIs & Services → Library**. Search for and click **Enable** on each of:
   - Cloud Run Admin API
   - Cloud Build API
   - Artifact Registry API
   - Compute Engine API *(needed because Load Balancer components are part of the Compute Engine API surface)*
   - Cloud Storage API

> **Console path:** Top nav → project dropdown → New Project, then **APIs & Services → Library**

---

## 7. Step 2 — Create the custom VPC and subnet

1. Left menu → **VPC network → VPC networks** → **Create VPC Network**.
2. Name: `traditional-arch-vpc`. Subnet creation mode: **Custom** (not Automatic — this is what makes it a deliberately-controlled network instead of Google's default layout).
3. Under "New subnet": name `traditional-arch-subnet`, region **us-central1** (pick one region and use it consistently for every resource in this guide), IP address range `10.10.0.0/24`. Click **Done**.
4. Click **Create**.

This network costs nothing by itself — charges only apply to specific things placed inside it (like a NAT gateway or a reserved-but-unused static IP), neither of which this design uses.

> **Console path:** Left menu → **VPC network → VPC networks → Create VPC Network**

---

## 8. Step 3 — Create the Cloud Storage bucket

1. Left menu → **Cloud Storage → Buckets** → **Create**.
2. Name it something **globally unique** (bucket names are unique across *all* of Google Cloud, not just your project) — e.g. `traditional-arch-data-<yourname>`.
3. Location type: **Region** → `us-central1` (same region as your subnet). Storage class: **Standard**. Access control: **Uniform**.
4. Click **Create**.

Write down the exact bucket name — you'll paste it into the backend's environment variable in the next step.

> **Console path:** Left menu → **Cloud Storage → Buckets → Create**

---

## 9. Step 4 — Deploy the backend to Cloud Run

1. Left menu → **Cloud Run → Create Service**.
2. Choose **"Continuously deploy from a repository (source or function)"** → **Set up with Cloud Build**.
3. Authenticate GitHub if prompted, then select the `Muhammad-Ali500/gcp-traditional-arch` repository, branch `main`.
4. Build type: **Dockerfile**. Source location: `/backend` (this tells Cloud Build to use `backend/Dockerfile`, ignoring the frontend folder entirely).
5. Service name: `backend`. Region: `us-central1`.
6. Authentication: **"Allow unauthenticated invocations"** — both the Load Balancer and the frontend need to reach this over plain HTTP without Google-level auth tokens.
7. Expand **"Container, Volumes, Networking, Security"** → **Container** tab → **Variables & Secrets** → **Add variable**:
   - Name: `GCS_BUCKET_NAME`
   - Value: the exact bucket name from Step 3
8. Click **Create**. Wait for the build + deploy to finish (watch the build log — this takes a minute or two the first time).
9. Once deployed, copy the service URL shown at the top of the page (looks like `https://backend-xxxxx-uc.a.run.app`) — you'll need it in Step 6.

> **Console path:** Left menu → **Cloud Run → Create Service → Continuously deploy from a repository**

**What just happened under the hood:** Cloud Run set up a Cloud Build *trigger* watching your GitHub repo, and created an Artifact Registry repository to store the images it builds. From now on, every push to `main` that touches the `backend/` folder will automatically rebuild and redeploy this service — no manual steps needed.

---

## 10. Step 5 — Grant the backend access to the bucket

By default, Cloud Run runs as the project's shared "Compute Engine default service account," which does **not** automatically have permission to read/write your bucket. Without this step, every call to the bucket will fail — and because the backend code was written defensively, it will **silently fall back to in-memory storage** instead of crashing, exactly like it did in local Docker testing. The app will *look* like it works, but nothing will actually be persisted to Cloud Storage.

1. Left menu → **Cloud Run** → click the `backend` service → **Security** tab. Note the **service account** email shown there (by default, something like `123456789-compute@developer.gserviceaccount.com`).
2. Left menu → **Cloud Storage → Buckets** → click your bucket → **Permissions** tab → **Grant Access**.
3. New principal: paste that service account email.
4. Role: **Storage Object Admin**.
5. Click **Save**.

> **How to confirm this worked:** after this step, submitting data through the app and then checking **Cloud Storage → your bucket → visits/** folder should show real `.json` files appearing. If that folder stays empty while the app still "works," this grant didn't take effect — double check the exact service account email matches.

---

## 11. Step 6 — Deploy the frontend to Cloud Run

1. Cloud Run → **Create Service** → same repository, source location `/frontend`, service name `frontend`, same region (`us-central1`).
2. Authentication: **"Allow unauthenticated invocations"**.
3. Variables & Secrets → **Add variable**:
   - Name: `BACKEND_URL`
   - Value: the backend URL you copied in Step 4
4. Expand **"Container, Volumes, Networking, Security"** → **Container** tab → **Container port** → set it to **`3000`**.
   > The frontend container listens on port 3000 (not Cloud Run's default of 8080) — the backend still listens on 8080, so only this field needs changing, only on the `frontend` service. If this is left at the default, Cloud Run will send traffic to a port nothing is listening on and every request will fail with a 503.
5. Click **Create**. Copy this service's own URL too.
6. **Open the frontend URL directly in your browser right now** (before touching the Load Balancer) — submit a name in the form and confirm it appears in the list below. This proves frontend → backend → storage works end-to-end *before* adding the Load Balancer on top, so if something's wrong, you know it's not a Load Balancer problem.

---

## 12. Step 7 — Build the HTTP Load Balancer

This is the most involved section — it wires the Load Balancer to both Cloud Run services via Serverless NEGs (see the [Glossary](#2-glossary--every-gcp-term-used-in-this-guide) if you need a refresher on what that means and why no VPC connector is required).

### 7a. Create the two backend services (and their Serverless NEGs)

1. Left menu → **Network Services → Load Balancing** → **Create Load Balancer**.
2. Choose: **Application Load Balancer (HTTP/HTTPS)** → **Public facing (external)** → **Global workload** → **Configure**.
3. Name the load balancer: `traditional-arch-lb`.
4. Under **Backend configuration** → **Create a backend service** → Backend type: **Serverless network endpoint group**.
5. Click **Create Serverless NEG**: name it `frontend-neg`, region `us-central1`, and select the **frontend** Cloud Run service as its endpoint. Save, then add this NEG as a backend.
6. Name this backend service `frontend-backend-service`. Leave Cloud CDN off (not needed for a test). Click **Create**.
7. Repeat the same process for the backend: create a second backend service named `backend-backend-service`, with a new Serverless NEG `backend-neg` pointing at the **backend** Cloud Run service.

### 7b. Routing rules (path-based)

1. Under **Host and path rules**, leave the default host as `*` (any hostname — we don't have a custom domain here, so this just means "match regardless of what hostname was used to reach the IP").
2. Add a new path rule: path `/api/*` → route to `backend-backend-service`.
3. Leave the default/unmatched-path rule pointing at `frontend-backend-service` — this is what serves every request that *isn't* `/api/*`.

### 7c. Frontend configuration (HTTP only — no certificate needed)

1. Under **Frontend configuration** → **Add frontend**.
2. Protocol: **HTTP** (not HTTPS — this is the step where SSL would normally be configured, and we're deliberately skipping it).
3. Port: **80**.
4. IP address: either create a new static external IP, or leave it as **Ephemeral** — either is fine for a short test. An ephemeral IP costs nothing extra as long as it's actively attached to this forwarding rule.
5. Network: select `traditional-arch-vpc` if prompted. If the wizard asks you to create a **proxy-only subnet**, accept the default settings (purpose: "Regional Managed Proxy" or similar) — this is a small internal IP range Google's load balancing infrastructure uses for itself; you never route your own traffic through it directly.
6. Click **Done**, review the full summary on the next screen, then click **Create**.

The load balancer typically takes **a few minutes** to fully provision and show healthy — this is normal, not a sign of an error.

> **Console path:** Left menu → **Network Services → Load Balancing → Create Load Balancer → Application Load Balancer**

---

## 13. Step 8 — Test it end-to-end

1. Once `traditional-arch-lb` shows a green/healthy status, go to its overview page and copy the **IP address** of the forwarding rule you created.
2. Open `http://<that-ip>/` in your browser — you should see the frontend page, served *through* the Load Balancer now (not its direct Cloud Run URL).
3. Submit a name — it should redirect back and show up in the list, proving the full chain works: Load Balancer → frontend (Cloud Run) → backend (Cloud Run) → Cloud Storage → back again.
4. Open `http://<that-ip>/api/visits` directly in your browser — you should get the same data as raw JSON straight from the backend, which proves the `/api/*` path rule is routing correctly to the right backend service.

> **Note:** global Load Balancer provisioning/propagation can take 2–5 minutes after creation before the IP actually responds. If you get a connection error or timeout immediately after creating it, wait a few minutes and try again before assuming something's misconfigured.

---

## 14. Step 9 — Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Load Balancer IP times out / connection refused | Still provisioning | Wait 2–5 minutes after creation, retry |
| Frontend loads, but shows "Could not reach backend" | `BACKEND_URL` env var wrong, or backend not deployed/unauthenticated calls blocked | Cloud Run → `frontend` → check the `BACKEND_URL` variable matches the real backend URL exactly (including `https://`); confirm `backend` allows unauthenticated invocations |
| App "works" but `visits/` folder in the bucket stays empty | IAM permission from Step 5 missing or wrong service account | Re-check the exact service account email on the backend's Security tab matches what you granted access to |
| `/api/*` requests return the frontend's HTML instead of JSON | URL map path rule misconfigured | Load Balancer → edit → Host and path rules → confirm `/api/*` points to `backend-backend-service`, not the default |
| Backend service's own direct Cloud Run URL works, but the same request via the Load Balancer IP fails | Serverless NEG or backend service health check still initializing | Load Balancer → Backend services → click the backend service → check its logged health status; this can take a minute or two after first creation |
| Cloud Build fails during deploy | A syntax/dependency error in the pushed code, or Cloud Build API not enabled | Cloud Build → History → open the failed build → read the actual error in the log |
| "Permission denied" enabling an API or creating a resource | Billing account not linked, or your account lacks Owner/Editor role on the project | Billing → confirm a billing account is attached; IAM & Admin → check your own role on the project |

---

## 15. Step 10 — Teardown (delete everything)

> **⚠️ Important:** the Load Balancer's forwarding rule is the one resource that bills by the hour ($0.025/hr) for as long as it exists. Delete it — and the rest, for cleanliness — as soon as you're done testing.

Delete in this order — each later step depends on the one before it already being gone:

1. **Network Services → Load Balancing** → click `traditional-arch-lb` → **Delete**. Confirm deleting the associated forwarding rule, target proxy, and URL map when prompted (the Console usually offers to clean these up together).
2. **Network Services → Load Balancing → Backend services** tab → delete `frontend-backend-service` and `backend-backend-service`, if either wasn't already removed with the load balancer itself.
3. **Compute Engine** (or **Network Services → Serverless NEGs**, depending on Console version) → delete `frontend-neg` and `backend-neg`, if any remain.
4. **Cloud Run** → delete the `frontend` and `backend` services.
5. **Cloud Storage → Buckets** → select your bucket → **Delete** (this permanently deletes the test data inside it too — that's expected and fine for a test project).
6. **VPC network → VPC networks** → delete `traditional-arch-subnet` first, then `traditional-arch-vpc` (subnets must be removed before the network itself can be deleted).
7. **Artifact Registry** → delete the repository Cloud Build created for your container images, if you don't want to keep them around.
8. **Double-check:** Network Services → Load Balancing shows nothing left, and Cloud Run shows no services — those two are the only resources that were ever actually capable of billing you meaningfully in this design.

---

## 16. Appendix: how a request actually flows through this system

Walking through one concrete example end-to-end, to tie every concept above together:

1. You type `http://34.120.x.x/` into your browser. That IP belongs to the **forwarding rule** you created in Step 7c.
2. The forwarding rule hands the request to its **target proxy**, which consults the **URL map**.
3. The URL map sees the path is `/` (not `/api/*`), so it routes to `frontend-backend-service`.
4. That backend service's **Serverless NEG** forwards the request to the **frontend Cloud Run service**.
5. Cloud Run spins up (or reuses) a container instance running `frontend/main.py`, which renders the page — but first, it makes its own HTTP call to `BACKEND_URL` (the backend's Cloud Run URL) to fetch the current list of visits.
6. That call lands on the **backend Cloud Run service** directly (not through the Load Balancer — the frontend talks to the backend's own URL, which is a separate, simpler path than the public-facing one).
7. The backend reads JSON files from the **Cloud Storage bucket** (using the IAM permission granted in Step 5) and returns them as a list.
8. The frontend renders that list into HTML and returns it — back through the same chain — to your browser.

When you submit the form, the same chain runs again, except the frontend sends a `POST` to the backend's `/api/visit` endpoint, which writes a new JSON file into the bucket before redirecting you back to `/`.
