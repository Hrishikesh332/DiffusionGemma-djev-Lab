
import json, random, time, os, asyncio, urllib.request
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

_TOK_CACHE = {}
# Empty thinking section the model expects before answering; without it the first answer slot
# sometimes holds neither label in its top-20 and the answer collapses to 50/50.
THINK = "<|channel>thought\n<channel|>"

# ---------------------------------------------------------------------------------------------
# Public gateway, spliced into server.py by patch_demos.py.
#
# Every request from outside the container passes through here first. Only what the Lab and the
# games need is reachable; every request body is size-checked and shape-checked; responses get
# security headers; nothing internal (docs, admin routes, version, error details) leaks out.
# Requests from inside the container (server.py's own calls to vLLM) are not restricted.
# ---------------------------------------------------------------------------------------------
import re as _re

_LOOPBACK = {"127.0.0.1", "::1"}
_PAGES = {"/", "/lab", "/lab.html", "/snake", "/snake.html", "/tetris", "/tetris.html", "/dino", "/dino.html"}
_PUBLIC_GET = _PAGES | {"/health", "/v1/models", "/metrics"}
_PUBLIC_POST = {"/v1/systemone", "/tokenize", "/v1/chat/completions"}
_MAX_BODY = 64 * 1024          # largest real request (tetris) is about 6 KB
_MAX_INFLIGHT = 160            # more than the GPU can use at once; protects memory from floods
_ID_RE = _re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
_METRIC_KEEP = ("vllm:request_success_total", "vllm:num_requests_running", "vllm:num_requests_waiting", "vllm:kv_cache_usage_perc")
_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-src 'self'; worker-src 'self' blob:; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")
_SEC_HEADERS = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
                (b"x-frame-options", b"DENY"), (b"cross-origin-opener-policy", b"same-origin"),
                (b"permissions-policy", b"camera=(), microphone=(), geolocation=()")]
_STRIP = {b"access-control-allow-origin", b"access-control-allow-credentials", b"access-control-allow-methods",
          b"access-control-allow-headers", b"access-control-expose-headers", b"server"}


class _Reject(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status, self.msg = status, msg


def _text(v, limit, what, allow_none=False):
    if v is None and allow_none:
        return
    if not isinstance(v, str):
        raise _Reject(400, f"{what} must be text")
    if len(v) > limit:
        raise _Reject(413, f"{what} is too long (max {limit} characters)")


def _int(v, lo, hi, what):
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise _Reject(400, f"{what} must be a whole number from {lo} to {hi}")


def _check_systemone(b):
    if not isinstance(b, dict):
        raise _Reject(400, "body must be a JSON object")
    extra = set(b) - {"state", "questions", "steps", "model", "samples", "seed"}
    if extra:
        raise _Reject(400, f"unknown field {sorted(extra)[0]!r}")
    state = b.get("state", "")
    if len(state if isinstance(state, str) else json.dumps(state)) > 8000:
        raise _Reject(413, "state is too long (max 8000 characters)")
    if "steps" in b:
        _int(b["steps"], 1, 4, "steps")
    qs = b.get("questions")
    if not isinstance(qs, dict) or not 1 <= len(qs) <= 8:
        raise _Reject(400, "questions must be an object with 1 to 8 questions")
    for qid, q in qs.items():
        if not _ID_RE.match(qid):
            raise _Reject(400, "each question id must start with a letter and use letters, digits or _ (max 40)")
        if not isinstance(q, dict):
            raise _Reject(400, f"question {qid} must be an object")
        kind = q.get("type", "choice")
        if kind not in ("choice", "score", "noul"):
            raise _Reject(400, f"question {qid}: type must be choice, score or noul")
        _text(q.get("instructions", ""), 2000, f"question {qid} instructions")
        crit = q.get("criteria", [])
        if kind == "noul":
            if len(json.dumps(crit)) > 2000:
                raise _Reject(413, f"question {qid} criteria are too long")
            continue
        items = list(crit.items()) if isinstance(crit, dict) else [(x, None) for x in crit] if isinstance(crit, list) else None
        top = 9 if kind == "score" else 12
        if items is None or not 2 <= len(items) <= top:
            raise _Reject(400, f"question {qid} needs 2 to {top} options")
        for name, desc in items:
            _text(name, 80, f"question {qid} option name")
            _text(desc, 300, f"question {qid} option description", allow_none=True)


def _check_chat(b):
    if not isinstance(b, dict):
        raise _Reject(400, "body must be a JSON object")
    extra = set(b) - {"model", "messages", "max_tokens", "logprobs", "top_logprobs", "vllm_xargs", "temperature"}
    if extra:
        raise _Reject(400, f"field {sorted(extra)[0]!r} is not allowed here")
    if b.get("model") != "djev-dgemma":
        raise _Reject(400, "model must be djev-dgemma")
    msgs = b.get("messages")
    if not isinstance(msgs, list) or not 1 <= len(msgs) <= 4:
        raise _Reject(400, "messages must be a list of 1 to 4 messages")
    total = 0
    for m in msgs:
        if not isinstance(m, dict) or set(m) - {"role", "content"} or m.get("role") not in ("system", "user"):
            raise _Reject(400, "each message needs a system or user role and text content")
        _text(m.get("content"), 20000, "message content")
        total += len(m["content"])
    if total > 20000:
        raise _Reject(413, "messages are too long (max 20000 characters)")
    _int(b.get("max_tokens"), 1, 64, "max_tokens")
    if "top_logprobs" in b:
        _int(b["top_logprobs"], 0, 20, "top_logprobs")
    if "logprobs" in b and not isinstance(b["logprobs"], bool):
        raise _Reject(400, "logprobs must be true or false")
    x = b.get("vllm_xargs")
    if not isinstance(x, dict) or set(x) - {"diffusion_seed_canvas", "diffusion_pinned", "diffusion_max_steps", "diffusion_read_only"}:
        raise _Reject(400, "vllm_xargs must hold only the diffusion read settings")
    if x.get("diffusion_read_only") is not True:
        raise _Reject(400, "only diffusion reads are allowed (diffusion_read_only must be true)")
    _int(x.get("diffusion_max_steps", 1), 1, 4, "diffusion_max_steps")
    canvas, pinned = x.get("diffusion_seed_canvas"), x.get("diffusion_pinned", [])
    if not isinstance(canvas, list) or len(canvas) != 128 or not all(isinstance(t, int) and 0 <= t < 300000 for t in canvas):
        raise _Reject(400, "diffusion_seed_canvas must be 128 token ids")
    if not isinstance(pinned, list) or len(pinned) > 128 or not all(isinstance(t, int) and 0 <= t < 128 for t in pinned):
        raise _Reject(400, "diffusion_pinned must be canvas positions 0 to 127")


def _check_tokenize(b):
    if not isinstance(b, dict) or set(b) - {"prompt", "add_special_tokens", "model"}:
        raise _Reject(400, "tokenize takes only prompt and add_special_tokens")
    _text(b.get("prompt"), 4000, "prompt")


_CHECKS = {"/v1/systemone": _check_systemone, "/v1/chat/completions": _check_chat, "/tokenize": _check_tokenize}


async def _json_reply(send, status, obj):
    body = json.dumps(obj).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


async def _read_body(receive):
    chunks, size = [], 0
    while True:
        m = await receive()
        if m["type"] == "http.disconnect":
            raise _Reject(400, "request ended early")
        chunk = m.get("body", b"")
        size += len(chunk)
        if size > _MAX_BODY:
            raise _Reject(413, "request is too large")
        chunks.append(chunk)
        if not m.get("more_body"):
            return b"".join(chunks)


def _replay(body, receive):
    sent = False

    async def again():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()   # wait for the real disconnect, so vLLM does not cancel the request early
    return again


# ---------------------------------------------------------------------------------------------
# Private audit log: one "DJEV_AUDIT {json}" line per outside request, written to the server's
# stdout (docker logs). Never shown in any page. Clients are pseudonymous: a salted hash of the IP
# and user agent.
# Request content (what people type) is never logged, only sizes and counts.
# A blocklist file (one client id per line) refuses listed clients; it is re-read every 10 s.
# ---------------------------------------------------------------------------------------------
import hashlib as _hashlib, os as _os, secrets as _secrets, sys as _sys, time as _time

_AUDIT_SALT = _os.environ.get("DJEV_AUDIT_SALT") or _secrets.token_hex(16)
_QUIET = {"/metrics", "/health", "/v1/models"}       # page polling; logged only when something goes wrong
_BLOCK_FILE = "/opt/dgemma/blocklist.txt"
_block = {"ids": set(), "checked": 0.0, "mtime": None}


def _hdr(scope, name):
    for k, v in scope.get("headers", []):
        if k.lower() == name:
            return v.decode("latin-1")
    return ""


def _blocked(cid):
    now = _time.monotonic()
    if now - _block["checked"] > 10:
        _block["checked"] = now
        try:
            mtime = _os.path.getmtime(_BLOCK_FILE)
            if mtime != _block["mtime"]:
                with open(_BLOCK_FILE) as f:
                    _block["ids"] = {l.split("#")[0].strip() for l in f if l.split("#")[0].strip()}
                _block["mtime"] = mtime
        except OSError:
            _block["ids"], _block["mtime"] = set(), None
    return cid in _block["ids"]


def _audit_start(scope):
    # The public link always arrives through Brev's proxy, which adds the visitor's IP. Requests without
    # it come from the GPU host itself (warm-ups, tests) and are labelled "local".
    ip = (_hdr(scope, b"cf-connecting-ip") or _hdr(scope, b"x-forwarded-for").split(",")[0]).strip()
    ua = _hdr(scope, b"user-agent")
    kind = ("browser" if "Mozilla/" in ua else "curl" if ua.lower().startswith("curl") else
            "python" if "python" in ua.lower() else "none" if not ua else "other")
    # IP + user agent: people sharing a venue's Wi-Fi still get separate ids, so blocking one device
    # does not block the room
    cid = _hashlib.sha256(f"{_AUDIT_SALT}|{ip}|{ua}".encode()).hexdigest()[:12] if ip else "local"
    return {"t": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            "id": cid,
            "cc": _hdr(scope, b"cf-ipcountry") or "-", "ua": kind,
            "m": scope.get("method", ""), "p": scope.get("path", "")[:80], "s": 0, "_t0": _time.perf_counter()}


def _track(send, rec):
    async def wrapped(m):
        if m["type"] == "http.response.start":
            rec["s"] = m.get("status", 0)
        await send(m)
    return wrapped


def _audit_end(rec):
    rec["ms"] = round((_time.perf_counter() - rec.pop("_t0")) * 1000)
    if rec["p"] in _QUIET and rec["s"] == 200:
        return
    try:
        print("DJEV_AUDIT " + json.dumps(rec, separators=(",", ":")), file=_sys.stdout, flush=True)
    except Exception:
        pass


def _secure(send, html):
    async def wrapped(m):
        if m["type"] == "http.response.start":
            headers = [(k, v) for k, v in m.get("headers", []) if k.lower() not in _STRIP] + _SEC_HEADERS
            if html:
                headers.append((b"content-security-policy", _CSP.encode()))
            m = dict(m, headers=headers)
        await send(m)
    return wrapped


class SystemOneMiddleware:
    def __init__(self, app):
        self.app = app
        self.pages = {
            "/": "lab.html", "/snake": "snake.html", "/snake.html": "snake.html",
            "/dino": "dino.html", "/dino.html": "dino.html",
            "/tetris": "tetris.html", "/tetris.html": "tetris.html",
            "/lab": "lab.html", "/lab.html": "lab.html",
        }

    _inflight = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or (scope.get("client") or ("", 0))[0] in _LOOPBACK:
            return await self._handle(scope, receive, send)
        method, path = scope["method"], scope["path"]
        rec = _audit_start(scope)
        send = _secure(_track(send, rec), html=method == "GET" and path in _PAGES)
        try:
            if _blocked(rec["id"]):
                raise _Reject(403, "access to this server has been removed")
            if method == "OPTIONS":
                await send({"type": "http.response.start", "status": 204, "headers": []})
                return await send({"type": "http.response.body", "body": b""})
            if method == "GET" and path in _PUBLIC_GET:
                if path == "/metrics":
                    return await self._public_metrics(scope, receive, send)
                return await self._handle(scope, receive, send)
            if method == "POST" and path in _PUBLIC_POST:
                body = await _read_body(receive)
                rec["in"] = len(body)
                try:
                    data = json.loads(body or b"null")
                except ValueError:
                    raise _Reject(400, "body must be valid JSON")
                _CHECKS[path](data)
                if path == "/v1/systemone":
                    rec["q"] = len(data.get("questions") or {})
                if SystemOneMiddleware._inflight >= _MAX_INFLIGHT:
                    raise _Reject(429, "the model is busy, try again in a moment")
                SystemOneMiddleware._inflight += 1
                try:
                    return await self._handle(scope, _replay(body, receive), send)
                finally:
                    SystemOneMiddleware._inflight -= 1
            raise _Reject(404, "not found")
        except _Reject as r:
            rec["why"] = r.msg[:80]
            return await _json_reply(send, r.status, {"error": r.msg})
        finally:
            _audit_end(rec)

    async def _public_metrics(self, scope, receive, send):
        # only the request counters the pages use; the rest of vLLM's metrics stay private
        inner = dict(scope, headers=[h for h in scope.get("headers", []) if h[0].lower() != b"accept-encoding"])
        status, chunks = [200], []

        async def capture(m):
            if m["type"] == "http.response.start":
                status[0] = m.get("status", 200)
            elif m["type"] == "http.response.body":
                chunks.append(m.get("body", b""))
        await self._handle(inner, receive, capture)
        text = b"".join(chunks).decode("utf-8", "replace")
        body = ("\n".join(l for l in text.splitlines() if l.startswith(_METRIC_KEEP)) + "\n").encode()
        await send({"type": "http.response.start", "status": status[0],
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    async def _handle(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
            
        req = Request(scope, receive)
        path = req.url.path

        if req.method == "GET" and path in self.pages:
            p = os.path.join("/opt/dgemma", self.pages[path])
            if not os.path.exists(p): p = os.path.join(os.getcwd(), self.pages[path])
            try:
                with open(p, "rb") as f:
                    return await HTMLResponse(content=f.read())(scope, receive, send)
            except Exception as e:
                return await HTMLResponse(content="page not available", status_code=500)(scope, receive, send)

        if req.method == "POST" and path == "/v1/systemone":
            try:
                body = await req.json()
            except:
                return await JSONResponse({"error": "invalid json"}, status_code=400)(scope, receive, send)
            
            t0 = time.time()
            qs = []
            questions_data = body.get("questions", {})
            if isinstance(questions_data, dict):
                for qid, qspec in questions_data.items():
                    qtype = qspec.get("type", "choice")
                    crit = qspec.get("criteria", [])
                    opts, labels = [], []
                    if qtype == "choice":
                        if isinstance(crit, list):
                            opts = [[str(x), None] for x in crit]
                        else:
                            opts = [[str(k), str(v) if v is not None else None] for k, v in crit.items()]
                        labels = [chr(97 + i) for i in range(len(opts))]
                    elif qtype == "score":
                        if isinstance(crit, list):
                            opts = [[str(x), str(x)] for x in crit]
                        else:
                            opts = []
                        labels = [str(i + 1) for i in range(len(opts))]
                    else: # noul
                        opts = [["yes", None], ["no", None]]
                        labels = ["yes", "no"]
                    qs.append({
                        "id": str(qid),
                        "type": qtype,
                        "instructions": qspec.get("instructions", ""),
                        "choices": opts,
                        "labels": labels
                    })
            elif isinstance(questions_data, list):
                qs = questions_data

            sys_text = "Answer a fixed set of questions about the state the user provides. Each question lists its allowed answers; reply with exactly one label per question.\n"
            for q in qs:
                sys_text += f"\nQuestion {q['id']}: {q.get('instructions', '').strip()}\n"
                for idx, (name, desc) in enumerate(q.get('choices', [])):
                    lbl = q.get('labels', [])[idx]
                    if q['type'] == "noul":
                        sys_text += f"  {lbl}\n"
                    elif desc:
                        sys_text += f"  {lbl}: {name} ({str(desc).strip()})\n"
                    else:
                        sys_text += f"  {lbl}: {name}\n"
            sys_text += '\nReply with one line per question, in this order, formatted as "id: label".'
            
            base_str = "\n".join(f"{q['id']}: {q['labels'][0]}" for q in qs)
            alt_str = "\n".join(f"{q['id']}: {q['labels'][min(1, len(q['labels']) - 1)]}" for q in qs)

            port = os.environ.get("PORT", "8080")
            cache_key = f"{base_str}|{alt_str}"
            if cache_key in _TOK_CACHE:
                base_toks, alt_toks = _TOK_CACHE[cache_key]
            else:
                def do_tok():
                    b_toks = json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/tokenize", data=json.dumps({"prompt": THINK + base_str, "add_special_tokens": False}).encode(), headers={"Content-Type": "application/json"})).read())["tokens"]
                    a_toks = json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/tokenize", data=json.dumps({"prompt": THINK + alt_str, "add_special_tokens": False}).encode(), headers={"Content-Type": "application/json"})).read())["tokens"]
                    return b_toks, a_toks
                try:
                    base_toks, alt_toks = await asyncio.to_thread(do_tok)
                    _TOK_CACHE[cache_key] = (base_toks, alt_toks)
                except Exception as e:
                    return await JSONResponse({"error": "the tokenizer is not available"}, status_code=500)(scope, receive, send)
            
            slot_positions = [i for i in range(len(base_toks)) if base_toks[i] != alt_toks[i]]
            seed_canvas = list(base_toks)
            
            rng = random.Random(42)
            for pos in slot_positions:
                seed_canvas[pos] = rng.randrange(262144)
            pinned = [i for i in range(len(base_toks)) if i not in set(slot_positions)]
            
            while len(seed_canvas) < 128:
                seed_canvas.append(1)

            state_val = body.get("state", "")
            user_text = state_val if isinstance(state_val, str) else json.dumps(state_val)

            def do_req():
                payload = {
                    "model": "djev-dgemma",
                    "messages": [{"role": "system", "content": sys_text}, {"role": "user", "content": user_text}],
                    "max_tokens": len(base_toks),
                    "logprobs": True,
                    "top_logprobs": 20,
                    "vllm_xargs": {
                        "diffusion_seed_canvas": seed_canvas,
                        "diffusion_pinned": pinned,
                        "diffusion_max_steps": int(body.get("steps", 1)),
                        "diffusion_read_only": True
                    }
                }
                rq = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
                                            data=json.dumps(payload).encode(),
                                            headers={"Content-Type": "application/json"})
                return json.loads(urllib.request.urlopen(rq, timeout=10).read())
            
            try:
                out = await asyncio.to_thread(do_req)
            except Exception as e:
                return await JSONResponse({"error": "the model did not answer in time"}, status_code=500)(scope, receive, send)

            import math
            answers = {}
            for q, pos in zip(qs, slot_positions):
                choices_array = out["choices"][0]["logprobs"]["content"]
                if pos >= len(choices_array):
                    lbl_map = {lbl: -25.0 for lbl in q["labels"]}
                else:    
                    lp_dict = choices_array[pos]["top_logprobs"]
                    lbl_map = {}
                    for top in lp_dict:
                        t_str = str(top["token"]).strip()
                        val = float(top["logprob"])
                        if t_str not in lbl_map or val > lbl_map[t_str]:
                            lbl_map[t_str] = val
                
                floor = (min(lbl_map.values()) if lbl_map else -20.0) - 5.0
                lp_t = [lbl_map.get(lbl, floor) for lbl in q["labels"]]
                mx = max(lp_t)
                ex = [math.exp(x - mx) for x in lp_t]
                probs = [e / sum(ex) for e in ex]
                
                if q["type"] == "choice":
                    p_dict = {name: round(p, 6) for (name, _), p in zip(q.get("choices", []), probs)}
                    best_idx = max(range(len(probs)), key=lambda i: probs[i])
                    answers[q["id"]] = {"choice": q.get("choices", [])[best_idx][0], "probabilities": p_dict, "confidence": round(probs[best_idx], 6)}
                elif q["type"] == "score":
                    p_dict = {str(i + 1): round(p, 6) for i, p in enumerate(probs)}
                    sc = sum((i + 1) * p for i, p in enumerate(probs))
                    answers[q["id"]] = {"score": round(sc, 6), "probabilities": p_dict, "confidence": round(max(probs), 6)}
                else:
                    answers[q["id"]] = {"noul": round(probs[0], 6), "probability": round(probs[0], 6)}
            
            total_ms = round((time.time() - t0) * 1000, 2)
            answers_json = {"model": "djev-dgemma", "answers": answers, "diagnostics": {"timing": {"total_ms": total_ms}, "request_id": out.get("id"), "served_by": "vllm djev-dgemma"}}
            return await JSONResponse(answers_json)(scope, receive, send)

        return await self.app(scope, receive, send)
