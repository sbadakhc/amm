#!/usr/bin/env bash
# Throwaway MinIO (S3-compatible) for local testing of s3:// image URLs -- see
# AGENTS.md §5 and docs/decisions/0006-s3-storage-self-hosted-minio.md.
#
# Usage:
#   scripts/dev-minio.sh up                  # start container, create bucket, print S3 env vars
#   scripts/dev-minio.sh upload-demo-images  # push images/*.png, print their s3:// URLs
#   scripts/dev-minio.sh down                # stop and remove the container
#
# Uses boto3 directly against MinIO's published host port rather than a second
# container on a shared Docker network -- no inter-container networking to get right.

set -euo pipefail

CONTAINER_NAME="amm-minio"
API_PORT="59000"
CONSOLE_PORT="59001"
ACCESS_KEY="amm"
SECRET_KEY="amm12345"
BUCKET="amm-listings"
ENDPOINT_URL="http://127.0.0.1:${API_PORT}"

cmd="${1:-}"

case "$cmd" in
  up)
    if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
      echo "Container $CONTAINER_NAME already exists -- run 'scripts/dev-minio.sh down' first."
      exit 1
    fi
    docker run -d --name "$CONTAINER_NAME" --network bridge \
      -e MINIO_ROOT_USER="$ACCESS_KEY" -e MINIO_ROOT_PASSWORD="$SECRET_KEY" \
      -p "${API_PORT}:9000" -p "${CONSOLE_PORT}:9001" \
      minio/minio server /data --console-address ":9001" >/dev/null

    echo "Waiting for MinIO to accept connections..."
    for _ in $(seq 1 15); do
      if curl -s "${ENDPOINT_URL}/minio/health/live" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done

    AWS_ACCESS_KEY_ID="$ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$SECRET_KEY" python3 -c "
import boto3
c = boto3.client('s3', endpoint_url='${ENDPOINT_URL}', region_name='us-east-1')
try:
    c.create_bucket(Bucket='${BUCKET}')
except c.exceptions.BucketAlreadyOwnedByYou:
    pass
"

    echo ""
    echo "MinIO is up. Set these in your shell (or .env):"
    echo "  export S3_ENDPOINT_URL=\"${ENDPOINT_URL}\""
    echo "  export AWS_ACCESS_KEY_ID=\"${ACCESS_KEY}\""
    echo "  export AWS_SECRET_ACCESS_KEY=\"${SECRET_KEY}\""
    echo "Bucket: ${BUCKET} -- console at http://127.0.0.1:${CONSOLE_PORT}"
    ;;
  upload-demo-images)
    AWS_ACCESS_KEY_ID="$ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$SECRET_KEY" python3 -c "
import boto3, glob, os
c = boto3.client('s3', endpoint_url='${ENDPOINT_URL}', region_name='us-east-1')
for path in sorted(glob.glob('images/*.png')):
    key = os.path.basename(path)
    c.upload_file(path, '${BUCKET}', key)
    print(f's3://${BUCKET}/{key}')
"
    ;;
  down)
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    echo "Removed $CONTAINER_NAME."
    ;;
  *)
    echo "Usage: $0 {up|upload-demo-images|down}"
    exit 1
    ;;
esac
