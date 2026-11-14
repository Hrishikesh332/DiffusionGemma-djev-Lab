#!/usr/bin/env bash
# One-command deploy of djev Lab on a Linux GPU host (NVIDIA Blackwell, about 40 GB of free GPU memory,
# for example an RTX PRO 6000). Safe to run again: each step skips what is already done.
#
#   ./deploy.sh
#
# What it does:
#   1. installs Docker, the NVIDIA container toolkit and Docker Compose if missing
#   2. creates .env with a private audit salt, and an empty blocklist.txt
#   3. downloads the pinned model weights (about 18 GB) into ./weights
#   4. builds the image (Dockerfile) and starts it (docker-compose.yml)
#   5. waits for it, warms up the GPU kernels and asks one real question
#
# Optional settings: DJEV_PORT (8080)  DJEV_NAME (djev)  WEIGHTS_DIR (./weights)  DJEV_BIND (0.0.0.0)
set -euo pipefail
cd "$(dirname "$0")"

MODEL_REPO="nvidia/diffusiongemma-26B-A4B-it-NVFP4"
MODEL_REV="ec4ff3df205028f4e81c954c2227f9312b3ec2ea"     # the Hugging Face revision this project was tested on
BASE_IMAGE="$(awk '/^FROM /{print $2; exit}' Dockerfile)"
export DJEV_PORT="${DJEV_PORT:-8080}" DJEV_NAME="${DJEV_NAME:-djev}" DJEV_BIND="${DJEV_BIND:-0.0.0.0}"
export WEIGHTS_DIR="$(mkdir -p "${WEIGHTS_DIR:-$PWD/weights}" && cd "${WEIGHTS_DIR:-$PWD/weights}" && pwd)"
say() { printf '\n== %s\n' "$*"; }

say "GPU"
nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader

say "Docker, NVIDIA container toolkit, Compose"
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sudo sh
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo -E docker"
if ! $DOCKER info 2>/dev/null | grep -qi nvidia; then
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
  sudo apt-get update -qq && sudo apt-get install -y -qq nvidia-container-toolkit
  sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
fi
$DOCKER compose version >/dev/null 2>&1 || { sudo apt-get update -qq && sudo apt-get install -y -qq docker-compose-plugin; }

say "private settings"
if ! grep -qs '^DJEV_AUDIT_SALT=' .env; then
  echo "DJEV_AUDIT_SALT=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')" >> .env
  chmod 600 .env
  echo "created .env (audit salt: keeps client ids stable; never commit it)"
fi
[ -f blocklist.txt ] || printf '# one client id per line; listed clients get 403 within 10 s\n' > blocklist.txt

say "model weights ($MODEL_REPO @ ${MODEL_REV:0:7})"
mkdir -p "$WEIGHTS_DIR/dgemma"
if [ ! -f "$WEIGHTS_DIR/dgemma/.done-$MODEL_REV" ]; then
  $DOCKER pull -q "$BASE_IMAGE" >/dev/null
  # run as the calling user so ./weights stays yours (deletable without sudo)
  $DOCKER run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e HF_HOME=/tmp/hf \
    -v "$WEIGHTS_DIR":/w --entrypoint python3 "$BASE_IMAGE" -c \
    "from huggingface_hub import snapshot_download; snapshot_download('$MODEL_REPO', revision='$MODEL_REV', local_dir='/w/dgemma')"
  touch "$WEIGHTS_DIR/dgemma/.done-$MODEL_REV"
fi
du -sh "$WEIGHTS_DIR/dgemma"

say "build and start"
$DOCKER compose up -d --build

say "waiting for the model (about a minute)"
for i in $(seq 1 90); do curl -sf -o /dev/null "localhost:$DJEV_PORT/health" && break; sleep 5; done
curl -sf -o /dev/null "localhost:$DJEV_PORT/health" || { echo "not healthy; see: $DOCKER logs $DJEV_NAME"; exit 1; }

say "warming up (the first calls compile GPU kernels)"
for round in 1 2 3; do
  seq 8 | xargs -P 8 -I{} curl -s -o /dev/null -m 60 "localhost:$DJEV_PORT/v1/systemone" \
    -H "Content-Type: application/json" --data @djev-patched/firewall.json
done
curl -s -m 30 "localhost:$DJEV_PORT/v1/systemone" -H "Content-Type: application/json" --data @djev-patched/firewall.json | head -c 200
echo

cat <<EOF

Ready.
  Lab      http://localhost:$DJEV_PORT/lab
  Games    http://localhost:$DJEV_PORT/snake  /tetris  /dino
  Report   $DOCKER logs $DJEV_NAME 2>&1 | grep DJEV_AUDIT | python3 tools/usage_report.py
  Stop     $DOCKER compose down
Share it through your provider's proxy (for Brev: brev ports open <instance> $DJEV_PORT --protocol http --public).
EOF
