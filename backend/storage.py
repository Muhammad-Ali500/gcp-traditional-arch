"""
Pluggable storage backend for "visit" records - lets the same backend code
deploy to either GCP (Cloud Storage) or AWS (S3) unchanged, selected purely
by which environment variable is set. Falls back to an in-memory list if
neither is configured or reachable, so the service always runs locally in
Docker with zero cloud credentials.

Precedence: GCS_BUCKET_NAME (GCP) > AWS_S3_BUCKET_NAME (AWS) > in-memory.
"""
import datetime
import json
import logging
import os
import uuid

logger = logging.getLogger("backend.storage")

GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "")
AWS_S3_BUCKET_NAME = os.environ.get("AWS_S3_BUCKET_NAME", "")


class _InMemoryBackend:
    name = "in-memory"

    def __init__(self):
        self._store = []

    def put(self, record):
        self._store.append(record)

    def list(self, limit=50):
        return list(reversed(self._store[-limit:]))


class _GCSBackend:
    name = "gcs"

    def __init__(self, bucket_name):
        from google.cloud import storage

        client = storage.Client()
        self._bucket = client.bucket(bucket_name)
        self._bucket.exists()  # cheap call to confirm it's actually reachable now

    def put(self, record):
        blob = self._bucket.blob(f"visits/{record['id']}.json")
        blob.upload_from_string(json.dumps(record), content_type="application/json")

    def list(self, limit=50):
        records = []
        for blob in self._bucket.list_blobs(prefix="visits/"):
            try:
                records.append(json.loads(blob.download_as_text()))
            except Exception:  # noqa: BLE001
                continue
        records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return records[:limit]


class _S3Backend:
    name = "s3"

    def __init__(self, bucket_name):
        import boto3

        self._bucket_name = bucket_name
        self._s3 = boto3.client("s3")
        self._s3.head_bucket(Bucket=bucket_name)  # confirm reachable + permitted now

    def put(self, record):
        self._s3.put_object(
            Bucket=self._bucket_name,
            Key=f"visits/{record['id']}.json",
            Body=json.dumps(record).encode("utf-8"),
            ContentType="application/json",
        )

    def list(self, limit=50):
        records = []
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket_name, Prefix="visits/"):
            for obj in page.get("Contents", []):
                try:
                    body = self._s3.get_object(Bucket=self._bucket_name, Key=obj["Key"])["Body"].read()
                    records.append(json.loads(body))
                except Exception:  # noqa: BLE001
                    continue
        records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return records[:limit]


def _build_backend():
    if GCS_BUCKET_NAME:
        try:
            backend = _GCSBackend(GCS_BUCKET_NAME)
            logger.info("Connected to GCS bucket '%s'", GCS_BUCKET_NAME)
            return backend
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "GCS bucket '%s' not reachable (%s) - falling back to in-memory storage.",
                GCS_BUCKET_NAME, exc,
            )
    elif AWS_S3_BUCKET_NAME:
        try:
            backend = _S3Backend(AWS_S3_BUCKET_NAME)
            logger.info("Connected to S3 bucket '%s'", AWS_S3_BUCKET_NAME)
            return backend
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "S3 bucket '%s' not reachable (%s) - falling back to in-memory storage.",
                AWS_S3_BUCKET_NAME, exc,
            )
    else:
        logger.info("No GCS_BUCKET_NAME or AWS_S3_BUCKET_NAME set - running with in-memory storage only.")
    return _InMemoryBackend()


_backend = _build_backend()


def backend_name():
    return _backend.name


def record_visit(name: str) -> dict:
    record = {
        "id": str(uuid.uuid4())[:8],
        "name": name,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }
    _backend.put(record)
    return record


def list_visits(limit: int = 50):
    return _backend.list(limit)
