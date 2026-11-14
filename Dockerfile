# djev Lab image: the pinned djev-run build (vLLM + the DiffusionGemma-Jev server) plus this project's
# hardened server.py, the Lab and the three game pages. Weights are not baked in: mount them at /mnt/model/dgemma.
#
#   docker build -t djev-lab .
#   (or simply ./deploy.sh, which also downloads the weights and starts everything)

# djev-run only publishes :latest and changes it often, so the exact build this project was tested on is pinned
FROM ghcr.io/taeold/djev-run@sha256:a352b97ab4ccf9c0d5caabbb7df16e0b40a801426cd8582c0e3fe81b2da16ac1

COPY djev-patched/server.py djev-patched/lab.html djev-patched/snake.html djev-patched/tetris.html \
     djev-patched/dino.html djev-patched/firewall.json /opt/dgemma/

ENV MODEL=/mnt/model/dgemma
EXPOSE 8080
# the base image's entrypoint copies the weights into /dev/shm and starts vLLM with the djev middleware
