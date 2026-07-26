# 0006. S3 image storage: boto3 against a configurable endpoint, self-hosted via MinIO for dev

## Status
Accepted

## Context
SPEC.md §3.1 specifies `s3://` image URLs in production; Evidence and Consistency
Agents previously raised `NotImplementedError` for that scheme and only supported
`file://` (used by `generate_synthetic_data.py` for local dev/demo). Implementing real
S3 support raised a question: does this require an actual AWS account to develop and
test against, matching this project's existing pattern of zero-cloud-dependency local
development (`scripts/dev-db.sh` for Postgres)?

"S3" is an API, not an AWS exclusive -- `boto3` talks to any S3-compatible store
(MinIO, Cloudflare R2, Backblaze B2, etc.) by pointing it at a custom `endpoint_url`.

## Decision
- `images.py`: a shared `fetch_image_bytes(url) -> (bytes, mime)` helper, consolidating
  logic that was previously duplicated (with drifting behavior) between
  `agents/evidence_agent.py` and `agents/consistency_agent.py`.
- `boto3` client configured via `S3_ENDPOINT_URL` (unset -> real AWS's default
  credential/endpoint resolution), `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `AWS_REGION`.
- `scripts/dev-minio.sh` -- a throwaway MinIO container for local dev/test, mirroring
  `scripts/dev-db.sh`'s pattern exactly (`up` / `down`, plus `upload-demo-images` to
  push the existing demo images and print their `s3://` URLs).

## Consequences
- Same code path works against real AWS S3 in production and MinIO in dev/test --
  only the env vars change, not the implementation.
- No AWS account needed to develop or test this feature. Verified against a real
  MinIO container: uploaded a real demo image, fetched it back via a real `s3://` URL
  through both Evidence Agent and Consistency Agent, and got results identical to the
  `file://` path on the same image.
- Caught a real bug in the process: `agents/evidence_agent.py` and
  `agents/consistency_agent.py` import `images` (a repo-root module) by absolute
  import, which breaks when either is run as a standalone script
  (`python3 agents/evidence_agent.py ...`, sys.path[0] becomes `agents/`, not the repo
  root) even though it works fine when imported normally (pytest, `pipeline.py`).
  Fixed with the same try/except sys.path fallback already used in
  `agents/decision_agent.py` for its `agents.policy_agent` import.
