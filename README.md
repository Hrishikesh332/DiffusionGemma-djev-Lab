# djev Lab

**Fast typed decisions with DiffusionGemma-Jev, live on a GPU.** Build a decision, test it, watch it moderate a chat, and see it play Snake, Tetris and Dino, with every answer coming straight from the model.

![Guided tour: building a chat moderator from scratch and running it live](docs/media/lab-tour.gif)

djev answers questions with a fixed set of answers (**Choice**, **Score**, **Yes / No**). It doesn't write text. It reads the input once and returns the probability of every possible answer, in about **60–80 ms on one RTX PRO 6000**. That makes it a good fit for the decisions a product makes thousands of times a day: routing, gating, triage and moderation.

---

## What's inside

### djev Lab: build a decision in four steps

The Lab walks you through **Use case → Questions → Test → Many at once**. A **Guide** button runs an 8-step tour that builds a chat moderator from scratch with you. Achievements unlock as you go.

| Use case | What djev decides |
|---|---|
| **Chat moderation** | starts empty; you build it with the guided tour |
| Agent firewall | is a web page, email or tool output trying to instruct your AI agent? |
| Model router | which handler should answer: small model, large model, search or database |
| Flood triage | which disaster posts need a rescuer first |
| Change gate | which pull requests need a security review |
| Support triage | route tickets, spot refund requests |
| Blank | your own idea |

### Chat moderation, like a Discord bot

When the questions are defined, the Test step turns into a live `#general` channel. Every message is labelled as it arrives: **allow** in green, **hide** in amber (the message is blurred), **escalate** in red (sent to a moderator). Press **Play chat** to stream a whole conversation.

![Discord-style chat moderation with colour-coded djev labels](docs/media/lab-chat.gif)

### Many at once

Run up to 100 inputs in parallel and see decisions per second, the typical time on the GPU, and every answer in a table.

![Batch run: six firewall checks with decisions per second](docs/media/lab-batch.gif)

### Three games, every move decided by djev

<table>
  <tr>
    <td width="33%"><img src="docs/media/snake.gif" alt="Snake: djev picks every move"></td>
    <td width="33%"><img src="docs/media/tetris.gif" alt="Tetris: djev picks every placement"></td>
    <td width="33%"><img src="docs/media/dino.gif" alt="Dino: djev decides jump, duck or run"></td>
  </tr>
  <tr>
    <td><b>Snake</b>: one decision per move, with the probability of each direction</td>
    <td><b>Tetris</b>: picks the placement and reads the board (strategy, health, fit)</td>
    <td><b>Dino</b>: jump, duck or run, several times a second</td>
  </tr>
</table>

---

## How it works

```mermaid
flowchart LR
  subgraph GPU["One NVIDIA RTX PRO 6000"]
    M["server.py middleware<br/>pages · decision API"] --> V["vLLM serving djev-dgemma<br/>nvidia/diffusiongemma-26B-A4B-it-NVFP4"]
  end
  U["Browser<br/>Lab · Snake · Tetris · Dino"] -->|"/v1/systemone · /v1/chat/completions"| M
```

- **One pass, every answer.** The server writes the question and its allowed labels into a fixed answer canvas. djev reads it once (`diffusion_read_only`, one step) and the server turns the model's scores at each answer slot into probabilities.
- **Stateless.** Each request carries its own questions and input. Nothing is stored on the server, so many people can experiment at once without seeing each other's work. Everything a person builds stays in their own browser.
- **Built on** [taeold/djev-run](https://github.com/taeold/djev-run) (vLLM with the djev middleware), with the fixes below.

### What this project changes in djev-run

| Change | Why |
|---|---|
| Empty "thinking" section before the answer canvas | The model expects to open with a thinking section, so the first answer was weak: sometimes neither label was in the top 20 and the answer collapsed to 50/50. With the fix, across 684 answers: **50/50 answers 4 → 0**, answers that changed between identical runs **3.5% → 1.0%**, mean confidence **0.908 → 0.960** |
| Every `/v1/systemone` answer includes `request_id` | proof that each answer is a fresh vLLM request |
| Game pages tokenize one prompt per call, and use the model name `djev-dgemma` | the upstream pages were rejected by this vLLM build and silently fell back to a **simulation** |
| Simulated fallbacks, placeholder probabilities and default answers removed | if the model can't answer, the page shows an error; nothing is ever faked |
| Tetris sends readable option descriptions | it was sending `[object Object]`, so the model was guessing |
| Dino's benchmark comparison and its "Hosted Jev 369 ms" baseline removed | they were fixed numbers from upstream, not measured on this server |
| `/lab`, a shared nav, one width for every page, a minimal look | one site instead of three unrelated demos |

## Proof that it's live

- Every Lab result shows its **vLLM request ID**, and the nav shows **live answers from djev**, read from vLLM's own `/metrics`.
- Tested on the live server: 36 Lab decisions across all use cases gave **36 unique request IDs** and the **vLLM counter rose by 36**. Snake: **49 moves = 49 requests**. Tetris and dino counts matched too.
- Freezing the model (`docker pause`) stopped **every** page: snake, tetris and dino made 0 moves for 10 seconds and the Lab got no answer. All of them resumed the moment it was unfrozen.

Check it yourself. Run this before and after playing a game; the number goes up by one per move:

```bash
curl -s https://<your-url>/metrics | grep 'request_success_total.*length'
```

## Security

The public link is safe to share with a workshop audience. Every request from outside the container passes through a gateway in `server.py` (source: `lab/gateway.py`):

- **Only what the Lab and games need is reachable:** the four pages, `/health`, `/v1/models`, `/metrics` (trimmed to request counters), `/v1/systemone`, `/tokenize` and `/v1/chat/completions`. The API explorer, version, admin and free-text routes (`/docs`, `/version`, `/scale_elastic_ep`, `/v1/completions`, `/v1/messages`, `/v1/responses` and others) return 404.
- **No free chatbot.** `/v1/chat/completions` accepts only short diffusion reads (`diffusion_read_only`, at most 64 tokens, model `djev-dgemma`), so it can't be used for general text generation.
- **Bounded requests:** a 64 KB body limit, at most 8 questions and 12 options, 8,000-character inputs, and a cap on requests in flight.
- **Browser protections:** no cross-site access (CORS), plus a content security policy, `nosniff`, no-referrer and anti-framing headers.
- **No leaks:** error messages carry no internal details, there are no secrets on the server, and nothing is stored between requests.

Requests the server makes to itself are not restricted. On the GPU host, only the proxied HTTPS link and key-only SSH are reachable from the internet.

### Private usage log and misuse report

The gateway writes one `DJEV_AUDIT` line per outside request to the server's own log. It is never shown in any page.

- Each line records the time, a **pseudonymous client ID**, country, client kind (browser, curl, python), method, path, status, time taken and request size.
- The client ID is a salted hash of the visitor's IP and user agent. People sharing a venue's Wi-Fi still get separate IDs, and no raw IP is stored. The salt is created by `deploy.sh` in the private `.env` file (never commit it), so IDs stay stable across restarts.
- **What people type is never logged.**
- The server's own warm-up calls appear as `local`.
- Logs rotate at 50 MB × 10 files.

Run the report on the GPU host (drop `--since 30m` to see everything):

```bash
docker logs --since 30m djev 2>&1 | grep DJEV_AUDIT | python3 tools/usage_report.py
```

Or from your laptop, for a Brev machine called `djev-run`:

```bash
brev exec djev-run "docker logs --since 30m djev 2>&1 | grep DJEV_AUDIT" < /dev/null | python3 tools/usage_report.py
```

It lists every client and flags possible misuse:

| Flag | Meaning |
|---|---|
| **flood** | over 1,500 requests in one minute |
| **probing** | 3 or more hits on blocked routes (`/docs`, `/version`, admin or free-text routes) |
| **bad requests** | 10 or more malformed, oversized or disallowed requests |
| **scripted use** | 50 or more model calls, mostly from curl, python or no user agent |

To cut someone off, add their ID to `blocklist.txt` in the repo folder on the GPU host. It takes effect within 10 seconds and needs no restart; that client then gets 403 everywhere. Append with `>>`; an editor that replaces the file won't be picked up until a restart:

```bash
echo "<client id>  # reason" >> blocklist.txt
```

## Performance

Load test with real requests captured from the games and the Lab (`loadtest/loadtest.py`), run on the GPU host:

| People at once | Decisions per second | Typical | Slowest 5% | Errors |
|---|---|---|---|---|
| 10 | 72 | 155 ms | 190 ms | 0 |
| 25 | 92 | 300 ms | ~400 ms | 0 |
| 40 | 110 (the limit) | 410 ms | ~500 ms | 0 |

On their own device, people also wait for their network round trip to the GPU's region. Deploy close to your audience.

---

## Run it

### Option A: any GPU machine, with Docker Compose (tested)

Needs a Linux host with an NVIDIA Blackwell GPU and about 40 GB of free GPU memory (an RTX PRO 6000 has 96 GB). `deploy.sh` installs Docker, the NVIDIA container toolkit and Compose if they're missing.

```bash
git clone <this repo> djev-lab && cd djev-lab
./deploy.sh
```

That's all. `deploy.sh`:
1. creates `.env` with a private audit salt, and an empty `blocklist.txt`
2. downloads the **pinned** model weights (`nvidia/diffusiongemma-26B-A4B-it-NVFP4` at revision `ec4ff3d`, 18 GB) into `weights/`
3. builds the image from the `Dockerfile` (the **pinned** djev-run base image plus `djev-patched/`)
4. starts it with `docker-compose.yml`, waits for it, warms it up and asks one real question

It's safe to run again; finished steps are skipped. Settings: `DJEV_PORT` (8080), `DJEV_NAME` (djev), `WEIGHTS_DIR` (./weights), `DJEV_BIND` (0.0.0.0).

Then share the port through your provider's proxy rather than opening it directly. Without a proxy, visitors can't be told apart in the audit log.

**On NVIDIA Brev**, from your laptop:

```bash
brev create djev-run --type massedcompute_RTXPro6000      # any RTX PRO 6000 type works
brev copy ./ djev-run:/home/shadeform/djev-lab/           # this repo
brev exec djev-run "cd ~/djev-lab && ./deploy.sh" < /dev/null
brev ports open djev-run 8080 --protocol http --public --hostname djev   # public HTTPS link
brev port-forward djev-run -p 8080:8080                   # or privately: http://localhost:8080/lab
```

> `brev exec` waits on stdin, so always add `< /dev/null`. A machine billed by the hour keeps billing until you `brev delete` it.

**Updating:** after changing `lab/`, `patch_demos.py` or a page, run `scripts/rebuild-pages.sh`, then `docker compose up -d --build` on the host.

### Option B: Google Cloud Run

One command deploys everything on a Cloud Run RTX PRO 6000. It scales to zero when idle, or stays warm for the session. See **[CLOUD_RUN.md](CLOUD_RUN.md)**. This path follows the codelab's Cloud Run recipe and has been dry-run, but not yet deployed end to end.

```bash
cloudrun/deploy.sh --warm --region europe-west4
```

### What is pinned

Every deploy gets exactly the tested versions:

| Piece | Pinned to | Where |
|---|---|---|
| Base image (vLLM + djev server) | `ghcr.io/taeold/djev-run@sha256:a352b97a…` | `Dockerfile`, `cloudrun/deploy.sh` |
| Upstream source the pages are built from | `taeold/djev-run` commit `35a6ff0` | `scripts/rebuild-pages.sh` |
| Model weights | `nvidia/diffusiongemma-26B-A4B-it-NVFP4` revision `ec4ff3d` | `deploy.sh` |

`scripts/rebuild-pages.sh` regenerates `djev-patched/` from the pinned upstream commit, and the result is byte-identical to the committed files.

## Project layout

```
djev-lab/
├── README.md            this file
├── CLOUD_RUN.md         Cloud Run guide
├── cloudrun/            deploy.sh and teardown.sh for Cloud Run
├── LICENSE, NOTICE      Apache-2.0 (from djev-run) and what this project changed
├── deploy.sh            one-command deploy on a GPU host
├── Dockerfile           pinned djev-run base image + djev-patched/
├── docker-compose.yml   GPU, weights mount, audit salt, blocklist, log rotation
├── djev-patched/        what the server runs: server.py, lab.html, snake/tetris/dino.html, firewall.json (warm-up)
├── lab/                 Lab source: template.html, presets.js (the use cases), gateway.py (security and audit log)
├── patch_demos.py       builds djev-patched/ from a djev-run checkout plus lab/
├── scripts/rebuild-pages.sh  regenerates djev-patched/ from the pinned upstream commit
├── loadtest/            load test with request bodies captured from the games
├── tools/               usage_report.py (misuse report), record-gif.mjs (GIFs)
└── docs/media/          GIFs used here
```

### Rebuild the pages

```bash
scripts/rebuild-pages.sh
```

It clones djev-run at the pinned commit and runs `patch_demos.py`, which applies every fix and the shared UI to the upstream game pages, builds `lab.html` from `lab/`, and patches `server.py`. It stops with an error if any expected piece of upstream code has changed.

### Re-record the GIFs

```bash
DJEV_URL=https://<your-url> node --experimental-websocket tools/record-gif.mjs tour docs/media/lab-tour.gif
# scenarios: tour, chat, batch, snake, tetris, dino
```

The recorder drives a throwaway headless Chrome profile (Google Chrome and ffmpeg required) against the live site, and turns the frames into a GIF with their real timing.

## Credits

- [taeold/djev-run](https://github.com/taeold/djev-run): serving djev on vLLM, and the original demo pages
- [mmastrac/djev](https://github.com/mmastrac/djev): Jev-style structured decisions on DiffusionGemma
- [nvidia/diffusiongemma-26B-A4B-it-NVFP4](https://huggingface.co/nvidia/diffusiongemma-26B-A4B-it-NVFP4): the model weights
- [trungdq88/jev-tetris](https://github.com/trungdq88/jev-tetris): the Tetris engine
- [virajbhartiya/laya-vs-jev](https://github.com/virajbhartiya/laya-vs-jev): the Dino game, its T-Rex engine and safety planner
- Google codelab [Getting started with Discriminative models](https://codelabs.developers.google.com/codelabs/discriminative-models): the workshop and its Cloud Run recipe
