# Deploy djev Lab on Google Cloud Run

This guide puts the whole project on **Cloud Run with one NVIDIA RTX PRO 6000**: the DiffusionGemma-Jev model (djev), the fixed `/v1/systemone` server, the djev Lab and the three game demos. It follows the Cloud Run path of the Google codelab [Getting started with Discriminative models](https://codelabs.developers.google.com/codelabs/discriminative-models) (its `scripts/setup_gemma_cloudrun.sh`), adapted for this project.

![The djev Lab guided tour, running live](docs/media/lab-tour.gif)

## What you get

| URL | What it is |
|---|---|
| `https://<service>.run.app/lab` | djev Lab: guided tour, use cases, chat moderation view, batch runs |
| `/snake`, `/tetris`, `/dino` | the three game demos, every move decided by djev |
| `/v1/systemone` | the Jev-compatible decision API (choice, score, yes/no) |
| `/v1/chat/completions` | the raw vLLM endpoint the games call |
| `/metrics` | vLLM's own counters, the source of the "live answers" number in the nav |

## How it fits together

```mermaid
flowchart LR
  subgraph CR["Cloud Run service · 1 × RTX PRO 6000"]
    S["server.py middleware<br/>/lab pages · /v1/systemone"] --> V["vLLM<br/>djev-dgemma"]
  end
  B["Browser<br/>Lab, Snake, Tetris, Dino"] -->|HTTPS| S
  HF["Hugging Face<br/>nvidia/diffusiongemma-26B-A4B-it-NVFP4"] --> J["Cloud Run job<br/>stages weights once"]
  J --> G[("Cloud Storage bucket<br/>dgemma/ weights · app/ files")]
  G -->|mounted at /mnt/gcs| S
```

On start, the container copies `server.py` and the four pages from the bucket into `/opt/dgemma`, then runs the djev-run entrypoint. That copies the 17.5 GB of weights into memory and starts vLLM.

## Before you start

1. **A Google Cloud project with billing.** In the codelab, `./setup_project.sh` creates one for you. Any project works.
2. **The `gcloud` CLI, signed in.** Cloud Shell already has it. Elsewhere:
   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT
   ```
3. **Cloud Run GPU quota: one RTX PRO 6000 in your region.** Most projects start with none. Request it on the [Quotas page](https://console.cloud.google.com/iam-admin/quotas): search for `NvidiaRtxPro6000GpuAllocNoZonalRedundancyPerProjectRegion`, pick your region and ask for 1. Check it with:
   ```bash
   cloudrun/deploy.sh --check-quota
   ```

### Pick a region

Cloud Run offers the RTX PRO 6000 in four regions. Pick the one nearest the people using the Lab, because every game move and decision makes a round trip.

| Region | Location |
|---|---|
| `europe-west4` (default) | Netherlands |
| `us-central1` | Iowa, USA |
| `asia-southeast1` | Singapore |
| `asia-south2` | Delhi, India |

> The smaller L4 GPU does not work: the djev-run image needs a newer NVIDIA driver than Cloud Run's L4 hosts have (CUDA error 803). The codelab found the same.

## Deploy, one command

From the repo folder:

```bash
cloudrun/deploy.sh --warm
```

`--warm` keeps one instance running, so nobody waits for a cold start. Use it on the day of the workshop. Without it the service scales to zero when idle and costs nothing, but the first request after an idle period takes about a minute.

To see every command first, without running anything:

```bash
cloudrun/deploy.sh --dry-run
```

The script is safe to run again: each step checks what already exists. It does this, in order:

| Step | What happens | Time |
|---|---|---|
| 0 | checks your RTX PRO 6000 quota in the region | seconds |
| 1 | enables Cloud Run, Cloud Storage and IAM | seconds |
| 2 | creates `gs://<project>-djev-weights` and lets the service read it | seconds |
| 3 | stages the weights from Hugging Face into the bucket with a one-off Cloud Run job | about 10 min, first time only |
| 4 | uploads `server.py`, the four pages and the warm-up request to `gs://…/app/` | seconds |
| 5 | deploys the service on one RTX PRO 6000 (20 vCPU, 80 GiB, concurrency 64, public) | 2–3 min |
| 6 | waits for `/health`, warms the GPU kernels, and asks one real question | about 1 min |

When it finishes, it prints the Lab, game and API URLs.

### Options

| Flag | Effect |
|---|---|
| `--region REGION` | one of the four regions above |
| `--warm` | min instances 1: no cold starts, billed while running |
| `--private` | require a Google identity token. Browsers can't add one, so the Lab pages then need a proxy; use this for API-only setups |
| `--fast-weights` | Direct VPC egress (`--network default --subnet default --vpc-egress all-traffic`), which loads the weights from the bucket at about 1 GB/s. Needs the `default` VPC network |
| `--project`, `--service`, `--bucket` | override the names |

## Deploy, step by step

The same steps as the script, if you'd rather run them yourself.

```bash
export PROJECT=$(gcloud config get-value project)
export REGION=europe-west4
export BUCKET=${PROJECT}-djev-weights
export SA="$(gcloud projects describe $PROJECT --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
```

**1. APIs and bucket**

```bash
gcloud services enable run.googleapis.com storage.googleapis.com iam.googleapis.com
gcloud storage buckets create gs://$BUCKET --location $REGION --uniform-bucket-level-access
gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
  --member serviceAccount:$SA --role roles/storage.objectAdmin
```

**2. Stage the weights (once)**

```bash
gcloud run jobs deploy djev-weights --region $REGION \
  --image python:3.12-slim --cpu 8 --memory 32Gi --task-timeout 3600 --max-retries 1 \
  --add-volume name=weights,type=cloud-storage,bucket=$BUCKET \
  --add-volume-mount volume=weights,mount-path=/mnt/gcs \
  --command bash \
  --args "^@@^-c@@pip install -q huggingface_hub hf_transfer && HF_HUB_ENABLE_HF_TRANSFER=1 python -c \"from huggingface_hub import snapshot_download; snapshot_download('nvidia/diffusiongemma-26B-A4B-it-NVFP4', local_dir='/tmp/dgemma')\" && mkdir -p /mnt/gcs/dgemma && cp -r /tmp/dgemma/. /mnt/gcs/dgemma/"
gcloud run jobs execute djev-weights --region $REGION --wait
```

**3. Upload this project's server and pages**

```bash
gcloud storage cp djev-patched/server.py djev-patched/*.html djev-patched/firewall.json gs://$BUCKET/app/
```

**4. Deploy the service**

```bash
gcloud beta run deploy djev-lab --region $REGION \
  --image ghcr.io/taeold/djev-run@sha256:a352b97ab4ccf9c0d5caabbb7df16e0b40a801426cd8582c0e3fe81b2da16ac1 \
  --gpu 1 --gpu-type nvidia-rtx-pro-6000 --no-gpu-zonal-redundancy \
  --cpu 20 --memory 80Gi --no-cpu-throttling \
  --concurrency 64 --min-instances 1 --max-instances 1 --port 8080 --timeout 300 \
  --add-volume name=weights,type=cloud-storage,bucket=$BUCKET,readonly=false,mount-options=enable-buffered-read=true \
  --add-volume-mount volume=weights,mount-path=/mnt/gcs \
  --startup-probe httpGet.path=/health,httpGet.port=8080,initialDelaySeconds=10,periodSeconds=5,timeoutSeconds=3,failureThreshold=190 \
  --set-env-vars MODEL=/mnt/gcs/dgemma \
  --command /bin/bash \
  --args="-c,cp -f /mnt/gcs/app/server.py /mnt/gcs/app/*.html /opt/dgemma/ && exec /entrypoint.sh" \
  --allow-unauthenticated
```

The image is pinned to the exact djev-run build this project was tested on, because djev-run only publishes `:latest` and changes it often.

**5. Warm it up and check it**

The first calls after a start compile GPU kernels and take 5–9 s each, so send a few before people arrive:

```bash
URL=$(gcloud run services describe djev-lab --region $REGION --format 'value(status.url)')
for i in 1 2 3; do seq 8 | xargs -P 8 -I{} curl -s -o /dev/null "$URL/v1/systemone" \
  -H 'Content-Type: application/json' --data @djev-patched/firewall.json; done
curl -s "$URL/v1/systemone" -H 'Content-Type: application/json' --data @djev-patched/firewall.json
```

You should get a JSON answer with `"injection"`, `"attack"`, `"action"` and `"risk"`, plus `diagnostics.request_id` (a `chatcmpl-…` ID from vLLM).

## Check that every answer is live

![Batch decisions, each with its own vLLM request](docs/media/lab-batch.gif)

- Each Lab result shows its **vLLM request ID**, and the nav shows **live answers from djev**, read from the server's own `/metrics`. Play a game and the number rises with every move.
- Count it yourself before and after a game. It goes up by one per move:
  ```bash
  curl -s "$URL/metrics" | grep 'request_success_total.*length'
  ```
- There is no simulated fallback in any page. If the model is unreachable, the Lab shows an error and the games stop.

## Using it from the codelab workbench

The service speaks the Discriminative model (Jev) API, so the codelab's workbench can use it as its DiffusionGemma backend. In the workbench's `.env`:

```bash
JEV101_MODEL=gemma
TYPESAFE_BASE_URL=https://djev-lab-xxxxx.europe-west4.run.app
JEV101_JEV_AUTH=none        # because this service is public
```

## Updating the pages or the server

The container copies the files from the bucket when it starts, so upload them and roll a new revision:

```bash
python3 -I patch_demos.py <djev-run checkout> djev-patched     # rebuild the pages, if you changed them
gcloud storage cp djev-patched/server.py djev-patched/*.html gs://$BUCKET/app/
gcloud run services update djev-lab --region $REGION --update-env-vars REFRESH=$(date +%s)
```

## Cost

| | Approximate cost |
|---|---|
| Service while an instance runs (`--warm`, or while serving) | about **$3.19 an hour** (1 × RTX PRO 6000, 20 vCPU, 80 GiB) |
| Service scaled to zero | $0 |
| Bucket with the weights | about 17.5 GB of Standard storage |
| Weights job | a few minutes of CPU, once |

On the day, deploy with `--warm`. Afterwards, run the deploy again without `--warm` (it scales to zero) or remove it.

## Capacity

Measured on the same GPU and server (see [README](README.md#performance)):

| People at once | Decisions per second | Typical answer on the server | Errors |
|---|---|---|---|
| 10 | 72 | 155 ms | 0 |
| 25 | 92 | 300 ms | 0 |
| 40 | 110 (the limit) | 410 ms | 0 |

Attendees also wait for their own round trip to the region, so pick the nearest one.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `no Cloud Run RTX PRO 6000 quota` | Request 1 GPU on the Quotas page for your region, then run the script again |
| Deploy fails with `allUsers` / domain restricted sharing | An organization policy blocks public services. Ask your admin, or deploy with `--private` and use the API with an identity token |
| `/health` never turns 200 | Read the logs: `gcloud run services logs read djev-lab --region $REGION`. A missing `gs://…/dgemma/config.json` means the weights job didn't finish |
| First answers take 5–9 s | Kernel compilation after a start. Run the warm-up loop above, or keep `--warm` |
| Pages look old after an update | Roll a new revision (see *Updating*) |
| L4 GPU fails with CUDA error 803 | Use the RTX PRO 6000; the image needs a newer driver |

## Remove it

```bash
cloudrun/teardown.sh          # deletes the service and the weights job, keeps the bucket
cloudrun/teardown.sh --all    # also deletes the bucket (weights and app files); asks first
```

The codelab's own cleanup (`scripts/teardown_gemma.sh`, project shutdown) still applies if you used its project.
