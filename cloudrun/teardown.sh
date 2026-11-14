#!/usr/bin/env bash
# Remove djev Lab from Cloud Run. By default deletes the service and the weights job and keeps the
# bucket (so a later deploy skips the 10-minute weight download). --all also deletes the bucket.
#
#   cloudrun/teardown.sh                 delete the service and the job
#   cloudrun/teardown.sh --all           also delete gs://<project>-djev-weights (weights + app files)
#   cloudrun/teardown.sh --dry-run       print every command, run nothing
#
# Options: --project ID  --region REGION  --service NAME  --bucket NAME
set -euo pipefail
PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
REGION="europe-west4"; SERVICE="djev-lab"; JOB="djev-weights"; BUCKET=""; ALL=0; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --bucket) BUCKET="$2"; shift 2 ;;
    --all) ALL=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n 2,9p "$0"; exit 0 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done
[ -n "$PROJECT" ] || { echo "No project. Pass --project ID."; exit 2; }
BUCKET="${BUCKET:-${PROJECT}-djev-weights}"
run() { if [ "$DRY" = 1 ]; then printf '  $ %s\n' "$*"; else "$@"; fi; }

echo "This deletes Cloud Run service '$SERVICE' and job '$JOB' in $PROJECT ($REGION)."
[ "$ALL" = 1 ] && echo "It also permanently deletes gs://$BUCKET (the 17.5 GB of weights and the app files)."
if [ "$DRY" = 0 ]; then
  read -r -p "Type yes to continue: " answer
  [ "$answer" = "yes" ] || { echo "Nothing deleted."; exit 0; }
fi
run gcloud run services delete "$SERVICE" --project "$PROJECT" --region "$REGION" --quiet || true
run gcloud run jobs delete "$JOB" --project "$PROJECT" --region "$REGION" --quiet || true
[ "$ALL" = 1 ] && run gcloud storage rm --recursive "gs://$BUCKET"
echo "Done."
