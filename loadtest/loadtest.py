"""Simulate workshop attendees hitting the djev server at the same time.

Each simulated person behaves like the real page they are on, using request bodies captured from
the live games (prompt text varies per request so the server cannot simply reuse a cached answer):
  snake  : 1 request in flight, next move ~50 ms after the answer          (snake.html loop)
  tetris : 1-2 in flight, long prompt (~1.7k tokens), next piece prefetched (tetris.html)
  dino   : 3 requests in flight, back to back                               (dino.html default)
  lab    : one /v1/systemone decision every 2-5 s, like a person clicking   (lab.html)
  batch  : a 6-line batch every 8 s, 6 at a time (a browser's limit)        (lab.html batch)

usage: python3 -I loadtest.py BASE_URL SECONDS "snake=3,tetris=2,dino=2,lab=2,batch=1" PAYLOAD_DIR
"""
import copy, http.client, json, random, re, statistics, sys, threading, time, urllib.parse

BASE, DUR, MIX, PDIR = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4]
U = urllib.parse.urlparse(BASE)
PAY = {k: json.load(open(f"{PDIR}/{k}.json")) for k in ("snake", "tetris", "dino")}
PAY["lab"] = json.load(open(f"{PDIR}/firewall.json"))
STOP = time.time() + DUR
lock = threading.Lock()
res = {k: {"lat": [], "err": 0, "errs": {}} for k in ("snake", "tetris", "dino", "lab", "batch")}
server = {"running": [], "waiting": [], "kv": []}


def conn():
    C = http.client.HTTPSConnection if U.scheme == "https" else http.client.HTTPConnection
    return C(U.hostname, U.port, timeout=60)


def body_for(kind):
    b = copy.deepcopy(PAY["lab" if kind == "batch" else kind])
    nonce = f" (attendee {random.randint(1, 10**9)})"
    if kind in ("lab", "batch"):
        b["state"] += nonce
        return "/v1/systemone", b
    b["messages"][1]["content"] += nonce
    return "/v1/chat/completions", b


def call(c, kind):
    path, b = body_for(kind)
    data = json.dumps(b).encode()
    t0 = time.time()
    try:
        c.request("POST", path, data, {"Content-Type": "application/json", "User-Agent": "djev-loadtest"})
        r = c.getresponse()
        txt = r.read()
        dt = time.time() - t0
        ok = r.status == 200 and b'"error"' not in txt[:200]
        if ok and kind in ("lab", "batch"):
            ok = b'"answers"' in txt
        with lock:
            if ok:
                res[kind]["lat"].append(dt)
            else:
                res[kind]["err"] += 1
                key = f"HTTP {r.status}"
                res[kind]["errs"][key] = res[kind]["errs"].get(key, 0) + 1
        return c, ok
    except Exception as e:
        with lock:
            res[kind]["err"] += 1
            key = type(e).__name__
            res[kind]["errs"][key] = res[kind]["errs"].get(key, 0) + 1
        try:
            c.close()
        except Exception:
            pass
        return conn(), False


def looper(kind, pause):
    c = conn()
    while time.time() < STOP:
        t0 = time.time()
        c, ok = call(c, kind)
        if not ok:
            time.sleep(0.5)
        wait = pause() - (time.time() - t0) if kind == "snake" else pause()
        if wait > 0:
            time.sleep(wait)


def batch_user():
    time.sleep(random.uniform(0, 4))
    while time.time() < STOP:
        ts = [threading.Thread(target=looper_once, args=("batch",)) for _ in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        time.sleep(8)


def looper_once(kind):
    call(conn(), kind)


def sampler():
    c = conn()
    while time.time() < STOP + 1:
        try:
            c.request("GET", "/metrics")
            txt = c.getresponse().read().decode()
            def val(name):
                m = re.search(rf"^{name}\{{[^}}]*\}} ([0-9.e+-]+)$", txt, re.M)
                return float(m.group(1)) if m else None
            run, wait = val("vllm:num_requests_running"), val("vllm:num_requests_waiting")
            kv = val("vllm:kv_cache_usage_perc") or val("vllm:gpu_cache_usage_perc")
            if run is not None:
                server["running"].append(run); server["waiting"].append(wait or 0); server["kv"].append(kv or 0)
        except Exception:
            c = conn()
        time.sleep(1)


people = dict((k, int(v)) for k, v in (p.split("=") for p in MIX.split(",")))
threads = [threading.Thread(target=sampler, daemon=True)]
for _ in range(people.get("snake", 0)):
    threads.append(threading.Thread(target=looper, args=("snake", lambda: 0.05)))
for _ in range(people.get("tetris", 0)):
    threads.append(threading.Thread(target=looper, args=("tetris", lambda: 0.0)))
    threads.append(threading.Thread(target=looper, args=("tetris", lambda: 0.25)))
for _ in range(people.get("dino", 0)):
    for _ in range(3):
        threads.append(threading.Thread(target=looper, args=("dino", lambda: 0.0)))
for _ in range(people.get("lab", 0)):
    threads.append(threading.Thread(target=looper, args=("lab", lambda: random.uniform(2, 5))))
for _ in range(people.get("batch", 0)):
    threads.append(threading.Thread(target=batch_user))

t_start = time.time()
[t.start() for t in threads]
[t.join() for t in threads if not t.daemon]
wall = time.time() - t_start


def pct(a, p):
    return sorted(a)[min(len(a) - 1, int(len(a) * p))] if a else float("nan")


print(f"\n{sum(people.values())} simulated people ({MIX}) for {wall:.0f} s against {BASE}\n")
print(f"{'part':8}{'people':>7}{'answers':>9}{'errors':>8}{'per person/s':>14}{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}")
tot_ok = tot_err = 0
for k in ("snake", "tetris", "dino", "lab", "batch"):
    n = people.get(k, 0)
    if not n:
        continue
    lat, err = res[k]["lat"], res[k]["err"]
    tot_ok += len(lat); tot_err += err
    print(f"{k:8}{n:>7}{len(lat):>9}{err:>8}{len(lat) / wall / n:>14.1f}{pct(lat, .5) * 1000:>9.0f}{pct(lat, .95) * 1000:>9.0f}{(max(lat) if lat else 0) * 1000:>9.0f}"
          + (f"   {res[k]['errs']}" if err else ""))
print(f"\ntotal: {tot_ok} answers, {tot_err} errors, {tot_ok / wall:.1f} answers/s overall")
if server["running"]:
    print(f"server: requests running avg {statistics.mean(server['running']):.1f} / max {max(server['running']):.0f} (slots 32), "
          f"queued avg {statistics.mean(server['waiting']):.1f} / max {max(server['waiting']):.0f}, "
          f"KV cache max {100 * max(server['kv']):.0f}%")
