#!/usr/bin/env bash
# Deploy djev Lab (DiffusionGemma-Jev + the Lab, Snake, Tetris and Dino pages) to Cloud Run
# on one NVIDIA RTX PRO 6000. Based on the Cloud Run path of the Google codelab
# "Getting started with Discriminative models" (scripts/setup_gemma_cloudrun.sh).
#
#   cloudrun/deploy.sh                          current gcloud project, europe-west4, public, scales to zero
#   cloudrun/deploy.sh --warm                   keep one GPU running (no cold start), for the workshop itself
#   cloudrun/deploy.sh --region us-central1     any region with Cloud Run RTX PRO 6000
#   cloudrun/deploy.sh --private                require a Google identity token (the Lab pages then need a proxy)
#   cloudrun/deploy.sh --fast-weights           Direct VPC egress for faster weight loading (needs the default VPC)
#   cloudrun/deploy.sh --dry-run                print every command, run nothing
#   cloudrun/deploy.sh --check-quota            only check GPU quota
#
# Options: --project ID  --region REGION  --service NAME  --bucket NAME
# Safe to run again: every step checks what already exists. Run it from the repo folder.
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
REGION="europe-west4"
SERVICE="djev-lab"
JOB="djev-weights"
BUCKET=""
WEIGHTS="nvidia/diffusiongemma-26B-A4B-it-NVFP4"
# The exact djev-run build the Brev deployment runs (djev-run publishes only :latest and changes it often).
IMAGE="ghcr.io/taeold/djev-run@sha256:a352b97ab4ccf9c0d5caabbb7df16e0b40a801426cd8582c0e3fe81b2da16ac1"
QUOTA_ID="NvidiaRtxPro6000GpuAllocNoZonalRedundancyPerProjectRegion"
WARM=0; PUBLIC=1; FAST=0; DRY=0; CHECK_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --bucket) BUCKET="$2"; shift 2 ;;
    --warm) WARM=1; shift ;;
    --private) PUBLIC=0; shift ;;
    --fast-weights) FAST=1; shift ;;
    --dry-run) DRY=1; shift ;;
    --check-quota) CHECK_ONLY=1; shift ;;
    -h|--help) sed -n 2,16p "$0"; exit 0 ;;
    *) echo "unknown option: $1 (try --help)"; exit 2 ;;
  esac
done

case "$REGION" in
  us-central1|europe-west4|asia-southeast1|asia-south2) ;;
  *) echo "Cloud Run RTX PRO 6000 GPUs are in us-central1, europe-west4, asia-southeast1 and asia-south2."; exit 2 ;;
esac
if [ -z "$PROJECT" ]; then
  [ "$DRY" = 1 ] && PROJECT="YOUR_PROJECT" || { echo "No project. Run: gcloud config set project YOUR_PROJECT (or pass --project)"; exit 2; }
fi
BUCKET="${BUCKET:-${PROJECT}-djev-weights}"
APP_FILES=(djev-patched/server.py djev-patched/lab.html djev-patched/snake.html djev-patched/tetris.html djev-patched/dino.html djev-patched/firewall.json)
for f in "${APP_FILES[@]}"; do [ -f "$f" ] || { echo "missing $f (run this from the repo folder)"; exit 2; }; done

say() { printf '\n· %s\n' "$*"; }
run() { if [ "$DRY" = 1 ]; then printf '  $ %s\n' "$*"; else "$@"; fi; }

# 0. GPU quota: most projects start with none, and asking first saves a long wait.
if [ "$DRY" = 0 ]; then
  gcloud services enable cloudquotas.googleapis.com --project "$PROJECT" --quiet >/dev/null 2>&1 || true
  QUOTA="$(gcloud beta quotas info describe "$QUOTA_ID" --service run.googleapis.com --project "$PROJECT" --format json 2>/dev/null \
    | python3 -c '
import json, sys
region = sys.argv[1]
try:
    info = json.load(sys.stdin)
except ValueError:
    print("unknown"); sys.exit()
for dim in info.get("dimensionsInfos", []):
    if region in dim.get("applicableLocations", []) or dim.get("dimensions", {}).get("region") == region:
        print(dim.get("details", {}).get("value", 0)); sys.exit()
print(0)' "$REGION")"
  if [ "$QUOTA" = "unknown" ]; then
    echo "· could not read the Cloud Run GPU quota; carrying on (the deploy will say if there is none)"
  elif [ "$QUOTA" -lt 1 ] 2>/dev/null; then
    echo "· $PROJECT has no Cloud Run RTX PRO 6000 quota in $REGION."
    echo "  Request 1 GPU at https://console.cloud.google.com/iam-admin/quotas?project=$PROJECT"
    echo "  (search $QUOTA_ID, region $REGION), then run this again."
    exit 3
  else
    echo "· Cloud Run GPU quota in $REGION: $QUOTA × RTX PRO 6000"
  fi
fi
[ "$CHECK_ONLY" = 1 ] && exit 0

say "project $PROJECT · region $REGION · service $SERVICE · bucket gs://$BUCKET"
[ "$DRY" = 1 ] && echo "  (dry run: nothing below is executed)"

# 1. APIs
say "enabling Cloud Run, Cloud Storage and IAM"
run gcloud services enable run.googleapis.com storage.googleapis.com iam.googleapis.com --project "$PROJECT"
if [ "$DRY" = 1 ]; then NUMBER="PROJECT_NUMBER"; else NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"; fi
RUNTIME_SA="${NUMBER}-compute@developer.gserviceaccount.com"

# 2. bucket for the weights and the app files
if [ "$DRY" = 0 ] && gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" >/dev/null 2>&1; then
  say "bucket gs://$BUCKET already exists"
else
  say "creating gs://$BUCKET in $REGION"
  run gcloud storage buckets create "gs://$BUCKET" --project "$PROJECT" --location "$REGION" --uniform-bucket-level-access
fi
run gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member "serviceAccount:$RUNTIME_SA" --role roles/storage.objectAdmin --format none --quiet

# 3. weights (~17.5 GB), staged once by a Cloud Run job so nothing large crosses your network
if [ "$DRY" = 0 ] && gcloud storage ls "gs://$BUCKET/dgemma/config.json" >/dev/null 2>&1; then
  say "weights already staged in gs://$BUCKET/dgemma"
else
  say "staging $WEIGHTS into gs://$BUCKET/dgemma with a Cloud Run job (about 10 minutes)"
  STAGE="pip install -q huggingface_hub hf_transfer && HF_HUB_ENABLE_HF_TRANSFER=1 python -c \"from huggingface_hub import snapshot_download; snapshot_download('$WEIGHTS', local_dir='/tmp/dgemma')\" && mkdir -p /mnt/gcs/dgemma && cp -r /tmp/dgemma/. /mnt/gcs/dgemma/ && ls -la /mnt/gcs/dgemma"
  run gcloud run jobs deploy "$JOB" --project "$PROJECT" --region "$REGION" \
    --image python:3.12-slim --cpu 8 --memory 32Gi --task-timeout 3600 --max-retries 1 \
    --add-volume "name=weights,type=cloud-storage,bucket=$BUCKET" \
    --add-volume-mount "volume=weights,mount-path=/mnt/gcs" \
    --command bash --args "^@@^-c@@$STAGE" --quiet
  run gcloud run jobs execute "$JOB" --project "$PROJECT" --region "$REGION" --wait
fi

# 4. this project's server.py and pages (copied into the container at startup)
say "uploading server.py and the Lab, Snake, Tetris and Dino pages to gs://$BUCKET/app/"
run gcloud storage cp "${APP_FILES[@]}" "gs://$BUCKET/app/"

# 5. the service
MIN=0; [ "$WARM" = 1 ] && MIN=1
AUTH=(--allow-unauthenticated); [ "$PUBLIC" = 0 ] && AUTH=(--no-allow-unauthenticated)
NET=(); [ "$FAST" = 1 ] && NET=(--network default --subnet default --vpc-egress all-traffic)
say "deploying $SERVICE on one RTX PRO 6000 (min instances $MIN, $([ "$PUBLIC" = 1 ] && echo public || echo private))"
run gcloud beta run deploy "$SERVICE" --project "$PROJECT" --region "$REGION" \
  --image "$IMAGE" \
  --gpu 1 --gpu-type nvidia-rtx-pro-6000 --no-gpu-zonal-redundancy \
  --cpu 20 --memory 80Gi --no-cpu-throttling \
  --concurrency 64 --min-instances "$MIN" --max-instances 1 --port 8080 --timeout 300 \
  --add-volume "name=weights,type=cloud-storage,bucket=$BUCKET,readonly=false,mount-options=enable-buffered-read=true" \
  --add-volume-mount "volume=weights,mount-path=/mnt/gcs" \
  --startup-probe "httpGet.path=/health,httpGet.port=8080,initialDelaySeconds=10,periodSeconds=5,timeoutSeconds=3,failureThreshold=190" \
  --set-env-vars "MODEL=/mnt/gcs/dgemma" \
  --command /bin/bash --args="-c,cp -f /mnt/gcs/app/server.py /mnt/gcs/app/*.html /opt/dgemma/ && exec /entrypoint.sh" \
  "${NET[@]+"${NET[@]}"}" "${AUTH[@]}" --quiet

if [ "$DRY" = 1 ]; then
  URL="https://$SERVICE-<hash>.$REGION.run.app"
else
  URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)')"
fi

# 6. wait, warm up (the first calls compile GPU kernels, 5-9 s each), and ask one real question
if [ "$DRY" = 0 ]; then
  HDR=(); [ "$PUBLIC" = 0 ] && HDR=(-H "Authorization: Bearer $(gcloud auth print-identity-token)")
  say "waiting for $URL (a cold start takes about a minute)"
  code=000
  for i in $(seq 1 90); do
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 20 "${HDR[@]+"${HDR[@]}"}" "$URL/health" || true)"
    [ "$code" = 200 ] && break
    sleep 5
  done
  [ "$code" = 200 ] || { echo "  no answer from /health (last status $code). Logs: gcloud run services logs read $SERVICE --region $REGION"; exit 1; }
  say "warming up"
  for round in 1 2 3; do
    seq 8 | xargs -P 8 -I{} curl -s -o /dev/null -m 60 "${HDR[@]+"${HDR[@]}"}" -X POST "$URL/v1/systemone" \
      -H "Content-Type: application/json" --data @djev-patched/firewall.json
  done
  say "asking one real question"
  curl -s -m 60 "${HDR[@]+"${HDR[@]}"}" -X POST "$URL/v1/systemone" -H "Content-Type: application/json" --data @djev-patched/firewall.json
  echo
fi

cat <<EOF

Done. djev Lab is live on Cloud Run:
  Lab     $URL/lab
  Games   $URL/snake   $URL/tetris   $URL/dino
  API     $URL/v1/systemone   (Jev-compatible; point the codelab workbench at it with TYPESAFE_BASE_URL=$URL)
Keep it warm for the session:   cloudrun/deploy.sh --warm   (about \$3.19 an hour while an instance runs)
Remove it:                      cloudrun/teardown.sh
EOF
