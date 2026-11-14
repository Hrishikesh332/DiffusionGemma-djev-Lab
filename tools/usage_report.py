"""Private usage report for djev: who is using the server and whether anyone looks like misuse.

Reads the "DJEV_AUDIT {json}" lines the server writes to its log. Clients are pseudonymous ids
(a salted hash of the IP); nothing people typed is logged.

  brev exec djev-run "docker logs djev 2>&1 | grep DJEV_AUDIT" < /dev/null | python3 tools/usage_report.py
  brev exec djev-run "docker logs --since 30m djev 2>&1 | grep DJEV_AUDIT" < /dev/null | python3 tools/usage_report.py

To cut someone off: add their id to ~/djev-patched/blocklist.txt on the server (one per line).
It takes effect within 10 seconds, no restart needed.
"""
import collections, json, sys
from datetime import datetime

# what counts as suspicious; a busy dino game alone can reach ~12 requests a second
FLOOD_PER_MIN = 1500       # one client above this in any minute
PROBES = 3                 # requests to routes that don't exist or are blocked (404)
REJECTS = 10               # malformed, oversized or disallowed requests (400/413)
SCRIPTED_MODEL_CALLS = 50  # model calls from curl/python/no user agent

recs = []
for line in sys.stdin:
    i = line.find("DJEV_AUDIT ")
    if i < 0:
        continue
    try:
        recs.append(json.loads(line[i + 11:]))
    except ValueError:
        pass
if not recs:
    sys.exit("No audit lines found. Is the server running the audit-log version?")

clients = collections.defaultdict(lambda: {"n": 0, "model": 0, "probe": 0, "reject": 0, "blocked": 0,
                                            "ua": collections.Counter(), "cc": collections.Counter(),
                                            "per_min": collections.Counter(), "paths": collections.Counter(),
                                            "why": collections.Counter(), "first": None, "last": None})
for r in recs:
    c = clients[r["id"]]
    c["n"] += 1
    c["ua"][r.get("ua", "?")] += 1
    c["cc"][r.get("cc", "-")] += 1
    c["per_min"][r["t"][:16]] += 1
    c["first"] = c["first"] or r["t"]
    c["last"] = r["t"]
    s = r.get("s", 0)
    if r.get("m") == "POST" and s == 200:
        c["model"] += 1
    if s == 404:
        c["probe"] += 1
        c["paths"][r.get("m", "") + " " + r.get("p", "")] += 1
    if s in (400, 413, 429):
        c["reject"] += 1
        c["why"][r.get("why", "?")] += 1
    if s == 403:
        c["blocked"] += 1

first, last = recs[0]["t"], recs[-1]["t"]
mins = max(1, (datetime.fromisoformat(last.rstrip("Z")) - datetime.fromisoformat(first.rstrip("Z"))).total_seconds() / 60)
print(f"djev usage  {first} to {last}  ({mins:.0f} min)")
print(f"{len(recs)} logged requests from {len(clients)} clients, "
      f"{sum(c['model'] for c in clients.values())} model calls, "
      f"{sum(c['probe'] for c in clients.values())} blocked-route hits, "
      f"{sum(c['reject'] for c in clients.values())} rejected requests\n")

flags = {}
for cid, c in clients.items():
    f = []
    peak = max(c["per_min"].values())
    if peak > FLOOD_PER_MIN:
        f.append(f"flood: {peak} requests in one minute")
    if c["probe"] >= PROBES:
        f.append(f"probing: {c['probe']} hits on blocked routes, e.g. {c['paths'].most_common(1)[0][0]}")
    if c["reject"] >= REJECTS:
        f.append(f"bad requests: {c['reject']}, mostly '{c['why'].most_common(1)[0][0]}'")
    scripted = c["ua"]["curl"] + c["ua"]["python"] + c["ua"]["none"] + c["ua"]["other"]
    if scripted and c["model"] >= SCRIPTED_MODEL_CALLS and scripted >= c["n"] / 2:
        f.append(f"scripted use: {c['model']} model calls from {c['ua'].most_common(1)[0][0]}")
    if f:
        flags[cid] = f

print(f"{'client':14}{'country':9}{'kind':9}{'requests':>9}{'model':>7}{'peak/min':>10}{'blocked rt':>12}{'rejected':>10}  active")
for cid, c in sorted(clients.items(), key=lambda kv: -kv[1]["n"]):
    mark = " !" if cid in flags else "  "
    print(f"{cid:12}{mark}{c['cc'].most_common(1)[0][0]:9}{c['ua'].most_common(1)[0][0]:9}{c['n']:>9}{c['model']:>7}"
          f"{max(c['per_min'].values()):>10}{c['probe']:>12}{c['reject']:>10}  {c['first'][11:16]}–{c['last'][11:16]}")

print()
if flags:
    print("Possible misuse:")
    for cid, f in flags.items():
        print(f"  {cid}: " + "; ".join(f))
    print("\nTo block one, on the server:  echo <client id> >> ~/djev-patched/blocklist.txt")
else:
    print("No misuse patterns found.")
