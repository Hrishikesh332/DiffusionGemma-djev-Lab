"""Patch djev-run demo pages so every move comes from the live model, with no simulated fallback.

Fixes:
1. /tokenize: pages send {"prompts": [a, b]}; this vLLM build only accepts {"prompt": a}.
   -> tokenize each string separately (same as server.py does for /v1/systemone).
2. model name: snake/dino send "jev-latest"; vLLM only serves "djev-dgemma".
3. snake/tetris: on any model error they silently switched to simulateSystemOne() for the rest
   of the session. -> surface the error and retry instead.
"""
import re
import sys
from pathlib import Path

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------------------------
# Shared UI: one nav bar on every page (Lab, Snake, Tetris, Dino) and a minimal theme per game.
# Only styling, labels and links change here; game logic is untouched.
# ---------------------------------------------------------------------------------------------
FONT_LINK = ('<link rel="preconnect" href="https://fonts.googleapis.com">\n'
             '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
             '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">\n')

NAV_CSS = """
  .djnav { background: #fff; border-bottom: 1px solid #ececec; position: sticky; top: 0; z-index: 50;
    font-family: Inter, -apple-system, "Helvetica Neue", "Segoe UI", Roboto, sans-serif; font-feature-settings: "calt" 0, "liga" 0; }
  .djnav-in { max-width: var(--djnav-max, 1100px); margin: 0 auto; padding: 10px var(--djnav-pad, 24px);
    display: flex; align-items: center; justify-content: space-between; gap: 16px; box-sizing: border-box; }
  .djnav-brand { font-weight: 700; font-size: 15px; color: #111; text-decoration: none; letter-spacing: -0.01em; }
  .djnav-seg { display: flex; align-items: center; gap: 2px; background: #f3f3f3; border-radius: 999px; padding: 4px; }
  .djnav-a { display: inline-block; padding: 6px 16px; border-radius: 999px; font-size: 14px; font-weight: 500; line-height: 1.4;
    color: #5e5e5e; text-decoration: none; white-space: nowrap; }
  .djnav-a:hover { color: #111; background: #e6e6e6; }
  .djnav-a.on { background: #76b900; color: #111; font-weight: 600; }
  .djnav-sep { width: 1px; height: 18px; background: #d4d4d4; margin: 0 6px; }
  .djnav-left { display: flex; align-items: baseline; gap: 18px; min-width: 0; }
  .djnav-live { font-size: 13px; color: #8a8a8a; white-space: nowrap; font-variant-numeric: tabular-nums; }
  .djnav-live b { color: #111; font-weight: 600; }
  .djnav-live.bump b { color: #4e7d00; }
  @media (max-width: 760px) { .djnav-live { display: none; } }
  @media (max-width: 520px) { .djnav-brand { display: none; } .djnav-in { justify-content: center; } .djnav-a { padding: 6px 12px; } }
"""


def nav_html(active):
    def a(href, label, key):
        on = ' class="djnav-a on" aria-current="page"' if key == active else ' class="djnav-a"'
        return f'<a href="{href}"{on}>{label}</a>'
    return ('<nav class="djnav" aria-label="djev pages"><div class="djnav-in">'
            '<div class="djnav-left"><a class="djnav-brand" href="/lab">djev</a>'
            '<span class="djnav-live" title="Answers the djev model has served on this server, read from its own metrics. '
            'It goes up with every game move and every decision."><b id="djnav-count">…</b> live answers from djev</span></div>'
            '<div class="djnav-seg">'
            + a("/lab", "Lab", "lab")
            + '<span class="djnav-sep" aria-hidden="true"></span>'
            + a("/snake", "Snake", "snake") + a("/tetris", "Tetris", "tetris") + a("/dino", "Dino", "dino")
            + '</div></div></nav>'
            + '<script>(function(){var el=document.getElementById("djnav-count"),last=-1;'
            + 'async function tick(){if(document.hidden)return;try{var r=await fetch("/metrics",{cache:"no-store"});'
            + 'if(!r.ok)return;var n=0,found=false;(await r.text()).split("\\n").forEach(function(l){if(l.indexOf("vllm:request_success_total")===0){found=true;n+=parseFloat(l.split(" ").pop())||0;}});'
            + 'if(!found)return;'
            + 'n=Math.round(n);if(last>=0&&n>last){el.parentNode.classList.add("bump");setTimeout(function(){el.parentNode.classList.remove("bump")},600);}'
            + 'last=n;el.textContent=n.toLocaleString();}catch(e){el.textContent="–";}}'
            + 'tick();setInterval(tick,5000);document.addEventListener("visibilitychange",tick);})();</script>')


SANS = 'Inter, -apple-system, "Helvetica Neue", "Segoe UI", Roboto, sans-serif'
MONO = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace'

THEME = {
    "snake.html": ("snake", "1128px", "24px", f"""
  html, body {{ background: #fff; color: #111; font-family: {SANS}; font-size: 14px; font-feature-settings: "calt" 0, "liga" 0; }}
  body {{ padding: 0; }}
  .wrap {{ width: auto; max-width: 1128px; padding: 32px 24px 48px; }}
  .main-grid {{ grid-template-columns: 520px minmax(0, 1fr); gap: 24px; }}
  .board-box, canvas#board {{ width: 520px; height: 520px; }}
  .right-col {{ height: 520px; gap: 16px; }}
  .probs-panel {{ height: auto; flex: 0 0 auto; gap: 10px; }}
  .json-panel {{ height: auto; flex: 1 1 auto; min-height: 0; }}
  @media (max-width: 1000px) {{ .main-grid {{ grid-template-columns: 1fr; }} .board-box, canvas#board {{ width: 100%; max-width: 520px; height: auto; aspect-ratio: 1 / 1; }} .right-col {{ height: auto; }} .json-panel {{ min-height: 180px; }} }}
  .topbar {{ height: auto; padding-bottom: 16px; margin-bottom: 20px; border-bottom-color: #ececec; }}
  .title {{ font-family: {SANS}; font-size: 24px; font-weight: 700; letter-spacing: -0.02em; color: #111; }}
  .status {{ font-family: {SANS}; color: #8a8a8a; font-size: 13px; width: auto; }}
  .dot {{ display: none; }}
  .metrics {{ font-family: {SANS}; font-size: 13px; }}
  .metric-label {{ color: #8a8a8a; font-weight: 400; }}
  .metric-val {{ color: #111; }}
  button {{ font-family: {SANS}; height: 36px; border-radius: 999px; }}
  #btn-start {{ width: auto; padding: 0 20px; background: #76b900; border: 0; color: #111; }}
  #btn-start:hover {{ background: #6aa600; }}
  #btn-reset {{ width: auto; padding: 0 18px; background: #f3f3f3; border: 0; color: #2e2e2e; }}
  #btn-reset:hover {{ background: #e9e9e9; }}
  .board-box {{ background: #fff; border: 1px solid #ececec; border-radius: 12px; }}
  .panel {{ background: #fff; border: 1px solid #ececec; border-radius: 12px; color: #111; }}
  .panel-head {{ color: #111; font-size: 13px; }}
  .panel-sub {{ font-family: {SANS}; color: #8a8a8a; font-size: 12px; }}
  .prob-row {{ font-family: {SANS}; font-size: 13px; }}
  .dir-name {{ color: #5e5e5e; }}
  .dir-name.chosen {{ color: #111; }}
  .bar-track {{ background: #f3f3f3; height: 6px; border-radius: 999px; }}
  .bar-fill {{ background: #cdcdcd; border-radius: 999px; }}
  .bar-fill.chosen {{ background: #76b900; }}
  .prob-pct {{ color: #111; }}
  .prob-pct.dim {{ color: #8a8a8a; }}
  .safety-tag {{ color: #4e7d00; }}
  .safety-tag.blocked {{ color: #b42318; }}
  pre#live-json {{ background: #f3f3f3; border: 0; border-radius: 8px; color: #2e2e2e; font-family: {MONO}; }}
  .j-key {{ color: #111; }} .j-str {{ color: #4e7d00; }} .j-num {{ color: #5e5e5e; }}
"""),
    "tetris.html": ("tetris", "1128px", "24px", f"""
  :root {{
    --bg-canvas: #ffffff; --bg-subtle: #fafafa; --bg-surface: #ffffff; --border: #ececec; --border-muted: #f3f3f3;
    --text-primary: #111111; --text-muted: #8a8a8a; --text-secondary: #5e5e5e;
    --accent-blue: #4e7d00; --accent-green: #76b900; --accent-amber: #9a6700; --accent-red: #b42318;
    --font-sans: {SANS}; --font-mono: {SANS};
  }}
  html, body {{ font-feature-settings: "calt" 0, "liga" 0; }}
  body {{ padding: 0; }}
  .wrap {{ width: auto; max-width: 1128px; padding: 32px 24px 48px; }}
  .topbar {{ height: auto; padding-bottom: 16px; margin-bottom: 20px; }}
  .title {{ font-size: 24px; font-weight: 700; letter-spacing: -0.02em; }}
  .dot {{ display: none; }}
  button {{ border-radius: 999px; }}
  #btn-start {{ background: #76b900; border-color: #76b900; color: #111; }}
  #btn-start:hover {{ background: #6aa600; border-color: #6aa600; color: #111; }}
  .overlay {{ background: rgba(255, 255, 255, 0.92); color: #111; }}
  .bar-fill {{ background: #cdcdcd; }}
  pre, code {{ font-family: {MONO}; }}
"""),
    "dino.html": ("dino", "1128px", "24px", f"""
  :root {{
    --page: #ffffff; --ink: #111111; --text: #5e5e5e; --faint: #8a8a8a; --rule: #ececec; --track: #f3f3f3;
    --warn: #b42318; --jev: #4e7d00; --laya: #5e5e5e; --green: #4e7d00; --sans: {SANS}; --mono: {SANS};
  }}
  body {{ font-family: {SANS}; max-width: none; padding: 0; margin: 0; font-feature-settings: "calt" 0, "liga" 0; }}
  .djpage {{ max-width: 1128px; margin: 0 auto; padding: 32px 24px 48px; box-sizing: border-box; }}
  .main-row {{ grid-template-columns: 640px minmax(0, 1fr); gap: 24px; }}
  .game-frame {{ max-width: 640px; }}
  @media (max-width: 1000px) {{ .main-row {{ grid-template-columns: 1fr; }} }}
  .arena-title {{ font-size: 24px; }}
  button {{ border-radius: 999px; }}
  button.primary {{ background: #76b900; border-color: #76b900; color: #111; }}
  button.primary:hover {{ background: #6aa600; border-color: #6aa600; }}
  pre, code, .payload-pre {{ font-family: {MONO}; }}
"""),
}


DINO_CREDIT = ('  <div style="font-size:12px;color:#8a8a8a;text-align:right;padding-top:16px;">\n'
               '    Built on <a href="https://github.com/virajbhartiya/laya-vs-jev" target="_blank" rel="noopener" '
               'style="color:#475569;">virajbhartiya/laya-vs-jev</a>\n'
               '  </div>')


def restyle(name, t):
    key, nav_max, nav_pad, css = THEME[name]
    head = (FONT_LINK if "family=Inter" not in t else "") + \
        f'<style id="djev-theme">\n  :root {{ --djnav-max: {nav_max}; --djnav-pad: {nav_pad}; }}{NAV_CSS}{css}</style>\n'
    t = replace(t, "</head>", head + "</head>", name)
    nav = nav_html(key)
    if name == "snake.html":
        t = replace(t, "<title>djev / snake</title>", "<title>djev Snake</title>", name)
        t = replace(t, '<body>\n  <div class="wrap">', f'<body>\n{nav}\n  <div class="wrap">', name)
        t = replace(t, '<span class="title">djev / snake</span>', '<span class="title">Snake</span>', name)
        t = replace(t, '<span>Last /v1/chat/completions Call</span>', '<span>Last model call</span>', name)
        t, n = re.subn(r"Demos: .*?&nbsp;\|&nbsp; ", "", t)
        assert n == 1, "snake footer links"
        t, n = re.subn(r'\s*&amp;\s*<a href="https://github.com/mizorewww/laya-coreml"[^>]*>[^<]*</a>', "", t)
        assert n == 1, f"snake laya credit {n}"
        assert not re.search(r"laya|viraj|bhartiya", t, re.I), "snake still mentions laya"
        t = replace(t, 'style="font-size:11px;color:#64748b;text-align:right;padding-top:2px;"',
                    'style="font-size:12px;color:#8a8a8a;text-align:right;padding-top:16px;"', name)
        t = replace(t, 'const CSS_SIZE = 360;', 'const CSS_SIZE = 520;', name)
        t = replace(t, 'fillStyle = "#f8fafc"', 'fillStyle = "#ffffff"', name)
        t = replace(t, 'strokeStyle = "#e2e8f0"', 'strokeStyle = "#f1f1f1"', name)
        t = replace(t, 'fillStyle = "#16a34a"', 'fillStyle = "#111111"', name)
        t = replace(t, 'fillStyle = isHead ? "#0969da" : "#3b82f6"', 'fillStyle = isHead ? "#4e7d00" : "#76b900"', name)
    elif name == "tetris.html":
        t = replace(t, "<title>djev (DiffusionGemma-Jev) / tetris</title>", "<title>djev Tetris</title>", name)
        t = replace(t, '<body>\n  <div class="wrap">', f'<body>\n{nav}\n  <div class="wrap">', name)
        t = replace(t, '<span class="title">djev <span class="model-tag">(DiffusionGemma-Jev)</span> / tetris</span>',
                    '<span class="title">Tetris</span>', name)
        t, n = re.subn(r'\s*<nav class="nav-links" aria-label="Demos">.*?</nav>', "", t, flags=re.S)
        assert n == 1, "tetris nav-links"
        t = replace(t, " · Model: djev (DiffusionGemma-Jev)", ", running on djev-dgemma", name)
        t = replace(t, '<span>Last /v1/chat/completions Call</span>', '<span>Last model call</span>', name)
        t, n = re.subn(r'const PIECE_COLORS = \{.*?\n\};',
                       'const PIECE_COLORS = {\n  I: "#76b900",\n  O: "#a7d65a",\n  T: "#4e7d00",\n  S: "#62a000",\n'
                       '  Z: "#2e4d00",\n  J: "#8a8a8a",\n  L: "#5e5e5e",\n};', t, flags=re.S)
        assert n == 1, "tetris piece colours"
        t = replace(t, 'ctx.fillStyle = "#0d0f17";', 'ctx.fillStyle = "#fafafa";', name)
        t = replace(t, 'ctx.strokeStyle = "#161a29";', 'ctx.strokeStyle = "#efefef";', name)
        t = replace(t, 'isFlash ? "#e2e8f0" : PIECE_COLORS[c]', 'isFlash ? "#d9eeb6" : PIECE_COLORS[c]', name)
        t = replace(t, 'PIECE_COLORS[game.current] : "#64748b"', 'PIECE_COLORS[game.current] : "#bdbdbd"', name)
        t = replace(t, 'nextCtx.fillStyle = "#0d0f17";', 'nextCtx.fillStyle = "#fafafa";', name)
    elif name == "dino.html":
        t = replace(t, "<title>LAYA vs JEV / DINO ARENA · Cloud Run djev-dgemma</title>", "<title>djev Dino</title>", name)
        t = replace(t, '<body>\n  <div class="arena-top">', f'<body>\n{nav}\n<div class="djpage">\n  <div class="arena-top">', name)
        t = replace(t, '\n  <script>\n', '\n</div>\n  <script>\n', name)
        t = replace(t, '<div class="arena-title">LAYA vs JEV &nbsp;/&nbsp; DINO ARENA</div>', '<div class="arena-title">Dino</div>', name)
        t = replace(t, '<div class="arena-subtitle">Authentic Chromium 2x sprite sheet + exact laya_mlx.trex physics &amp; safety planner on Cloud Run NVIDIA RTX PRO 6000</div>',
                    '<div class="arena-subtitle">djev decides whether the dinosaur jumps, ducks or keeps running, several times a second.</div>', name)
        t, n = re.subn(r'\s*<a href="/(snake|tetris)"[^>]*>(Snake|Tetris) Demo \(/(snake|tetris)\)</a>', "", t)
        assert n == 2, f"dino demo links {n}"
        t = t.replace("Cloud Run RTX PRO 6000 · ", "RTX PRO 6000 · ")
        t = replace(t, "to stream live decisions from Cloud Run djev-dgemma...", "to stream live decisions from djev-dgemma.", name)
        assert "Cloud Run" not in t, "dino Cloud Run text"
        # no mention of the third-party "laya-vs-jev" project or its author, visible or in the source
        t, n = re.subn(r'\s*<a href="https://github.com/virajbhartiya/laya-vs-jev"[^>]*>[^<]*</a>', "", t)
        assert n == 1, f"dino credit link {n}"
        t = replace(t, "Auto-Restart &amp; Count Deaths (laya-vs-jev Arena Mode)", "Auto-restart and count deaths", name)
        t = t.replace("laya_mlx/trex/", "trex/").replace("--laya", "--ref")
        assert not re.search(r"laya|viraj|bhartiya", t, re.I), "dino still mentions laya"
        # the one exception: a source credit at the bottom, styled like snake's
        t = replace(t, '\n</div>\n  <script>\n', f'\n{DINO_CREDIT}\n</div>\n  <script>\n', name)
        assert t.count(DINO_CREDIT) == 1 and not re.search(r"laya|viraj|bhartiya", t.replace(DINO_CREDIT, ""), re.I), \
            "dino mentions laya outside its credit"
        # no benchmark: drop the "Benchmark comparison" chart and the Hosted Jev 369 ms baseline
        t, n = re.subn(r'\n    <div class="chart-card">\n      <div class="chart-header">\n'
                       r'        <span class="panel-heading" style="margin:0;">BENCHMARK COMPARISON \(SEED 7\)</span>'
                       r'.*?</canvas>\n    </div>', "", t, flags=re.S)
        assert n == 1, f"dino benchmark card {n}"
        t = replace(t, 'grid-template-columns: repeat(3, 1fr);', 'grid-template-columns: repeat(2, 1fr);', name)
        t = replace(t, '\n        <span style="font-size:11px;color:var(--text);">p50 vs 369 ms Hosted Jev</span>', "", name)
        t, n = re.subn(r'      // Draw 369ms TypeSafe Hosted Jev baseline dashed line\n.*?'
                       r'lctx\.fillText\("Hosted Jev baseline: 369 ms", 38, yBase - 5\);\n\n', "", t, flags=re.S)
        assert n == 1, f"dino latency baseline {n}"
        t, n = re.subn(r'\n\n      // 3\. Head-to-Head bar comparison chart\n.*?\n      \}\);\n(?=    \}\n)', "\n", t, flags=re.S)
        assert n == 1, f"dino comparison chart code {n}"
        assert not re.search(r"compareChart|Hosted Jev|369|BENCHMARK", t), "dino still has the benchmark"
        t = replace(t, 'sctx.strokeStyle = "#eb6834";', 'sctx.strokeStyle = "#76b900";', name)
        t = replace(t, 'lctx.strokeStyle = "#eb6834";', 'lctx.strokeStyle = "#76b900";', name)
    return t


def build_lab(out):
    t = (HERE / "lab" / "template.html").read_text()
    presets = (HERE / "lab" / "presets.js").read_text().rstrip("\n")
    for a, b in (("__PRESETS__", presets), ("__DJNAV__", nav_html("lab")), ("__DJNAV_CSS__", NAV_CSS.rstrip("\n"))):
        assert t.count(a) == 1, a
        t = t.replace(a, b)
    (out / "lab.html").write_text(t)
    print("built lab.html")
dst.mkdir(parents=True, exist_ok=True)

TOK_RE = re.compile(
    r'const tokRes = await fetch\((?P<url>[^,]+), \{\s*'
    r'method: "POST",\s*'
    r'headers: \{ "Content-Type": "application/json" \},\s*'
    r'body: JSON\.stringify\(\{ prompts: \[baseStr, altStr\], add_special_tokens: false \}\)(?P<sig>,\s*signal,)?\s*'
    r'\}\);\s*'
    r'if \(!tokRes\.ok\) throw new Error\([^;]+\);\s*'
    r'const tokData = await tokRes\.json\(\);'
)


def tok_sub(m):
    sig = ", signal" if m.group("sig") else ""
    return (
        "const tokData = { tokens: await Promise.all([THINK + baseStr, THINK + altStr].map(async (prompt) => {\n"
        f"      const r = await fetch({m.group('url')}, {{ method: \"POST\", headers: {{ \"Content-Type\": \"application/json\" }}, body: JSON.stringify({{ prompt, add_special_tokens: false }}){sig} }});\n"
        "      if (!r.ok) throw new Error(`tokenize HTTP ${r.status}`);\n"
        "      return (await r.json()).tokens;\n"
        "    })) };"
    )


def replace(text, old, new, name, count=1):
    n = text.count(old)
    assert n == count, f"{name}: expected {count} of {old[:60]!r}, found {n}"
    return text.replace(old, new)


for name in ["snake.html", "tetris.html", "dino.html"]:
    t = (src / name).read_text()
    t, n = TOK_RE.subn(tok_sub, t)
    assert n == 1, f"{name}: tokenize block matches = {n}"
    # empty "thinking" section the model expects before its answer; without it the first answer slot is weak
    t = replace(t, "<script>\n", "<script>\nconst THINK = \"<|channel>thought\\n<channel|>\";\n", name)

    if name == "snake.html":
        t = replace(t, 'model: payload.model || "djev-dgemma",', 'model: "djev-dgemma",', name)
        t = replace(
            t,
            """  } catch (err) {
    const ms = Math.max(11, Math.round(performance.now() - t0));
    sandboxFallback = true;
    return { data: simulateSystemOne(state), ms };
  }""",
            """  } catch (err) {
    sandboxFallback = true;
    throw err;
  }""",
            name,
        )
        t = replace(
            t,
            "  const { data, ms } = await requestDecision(state);\n",
            """  let data, ms;
  try {
    ({ data, ms } = await requestDecision(state));
  } catch (err) {
    setStatus(`Model error: ${err.message} (retrying)`, "");
    await new Promise(r => setTimeout(r, 500));
    inflight = false;
    return;
  }
""",
            name,
        )
        t = replace(
            t,
            'setStatus(sandboxFallback ? "Running (sim)" : "Running (live)", "live");',
            'setStatus("Running (live model)", "live");',
            name,
        )
        t = replace(
            t,
            '<a href="/tetris" style="color:#475569;">/tetris</a>',
            '<a href="/tetris" style="color:#475569;">/tetris</a> &middot; <a href="/lab" style="color:#2563eb;font-weight:600;">/lab</a>',
            name,
        )

    if name == "tetris.html":
        t = replace(t, 'model: request.model || "djev-dgemma",', 'model: "djev-dgemma",', name)
        # placement question's instructions is {question, priorities[]}; it crashed on .trim()
        t = replace(
            t,
            'qs.push({ id: qid, type: qtype, instructions: qspec.instructions || "", choices: opts, labels });',
            'const ins = qspec.instructions;\n'
            '        const insText = typeof ins === "string" ? ins\n'
            '          : ins && ins.question ? ins.question + (Array.isArray(ins.priorities) ? "\\nPriorities:\\n" + ins.priorities.map(p => "- " + p).join("\\n") : "")\n'
            '          : ins ? JSON.stringify(ins) : "";\n'
            '        qs.push({ id: qid, type: qtype, instructions: insText, choices: opts, labels });',
            name,
        )
        # placement option descriptions are objects; they reached the model as "[object Object]"
        t = replace(
            t,
            "else if (desc) sysText += `  ${lbl}: ${name} (${String(desc).trim()})\\n`;",
            "else if (desc) sysText += `  ${lbl}: ${name} (${typeof desc === \"object\" ? Object.entries(desc).map(([k, v]) => `${k}: ${v}`).join(\"; \") : String(desc).trim()})\\n`;",
            name,
        )
        t = replace(
            t,
            "      if (res.ok) {\n        const data = await res.json();",
            "      if (!res.ok) throw new Error(`HTTP ${res.status}`);\n      {\n        const data = await res.json();",
            name,
        )
        t = replace(
            t,
            """    } catch (e) {
      if (signal?.aborted) throw e;
      sandboxFallback = true;
    }
  }
  const sim = simulateSystemOne(gameInfo, placements);
  return { response: sim, latencyMs: Math.round(performance.now() - t0), live: false };""",
            """    } catch (e) {
      throw e;
    }
  }
  throw new Error("djev model call failed");""",
            name,
        )
        t = replace(
            t,
            """      if (signal.aborted) return;
      setStatus("Retry", "warn");""",
            """      if (signal.aborted) return;
      setStatus(`Model error: ${e.message} (retrying)`, "warn");""",
            name,
        )

    if name == "dino.html":
        t = replace(t, 'model: reqPayload.model || "djev-dgemma",', 'model: "djev-dgemma",', name)

    # 4. djev only: no "jev-latest" (or other non-djev model names) anywhere in the pages.
    t = t.replace('model: "jev-latest"', 'model: "djev-dgemma"').replace('model: "dgemma"', 'model: "djev-dgemma"')
    if name == "tetris.html":
        t = replace(t, '<option value="jev" selected>', '<option value="djev" selected>', name)
        t = replace(t, 'mode === "jev"', 'mode === "djev"', name, count=2)
        t = replace(t, 'renderProbabilityPanel(dec, "jev")', 'renderProbabilityPanel(dec, "djev")', name)

    # 5. djev only, airtight: delete the unused simulators, tetris's Heuristic mode, placeholder
    #    probabilities shown before the first call, and default answers used if a reply was empty.
    t, n = re.subn(r"\nfunction simulateSystemOne\([^)]*\) \{\n.*?\n\}\n", "\n", t, flags=re.S)
    assert n == (1 if name in ("snake.html", "tetris.html") else 0), f"{name}: simulateSystemOne removed {n}"
    if name == "snake.html":
        t = replace(t, "let sandboxFallback = false;\n", "", name)
        t = replace(t, "    sandboxFallback = false;\n", "", name)
        t = replace(t, "    sandboxFallback = true;\n", "", name)
        t = replace(
            t,
            '  updateUI(st, { up: 0.25, down: 0.25, left: 0.0, right: 0.50 }, "right", 0);',
            '  updateUI(st, { up: 0, down: 0, left: 0, right: 0 }, "", 0);\n'
            '  $("choice-badge").textContent = "waiting for djev";\n'
            '  $("live-json").textContent = "No call yet. Press Start.";',
            name,
        )
        t = replace(
            t,
            "  const moveAns = (data.answers && data.answers.move) || {};\n"
            "  const probs = moveAns.probabilities || { up: 0.25, down: 0.25, left: 0.25, right: 0.25 };\n"
            "  const rawChoice = moveAns.choice || DIRS.reduce((a, b) => (probs[a] >= probs[b] ? a : b));\n",
            "  const moveAns = data.answers && data.answers.move;\n"
            "  if (!moveAns || !moveAns.probabilities || !moveAns.choice) {\n"
            '    setStatus("Model error: djev returned no move (retrying)", "");\n'
            "    inflight = false;\n"
            "    return;\n"
            "  }\n"
            "  const probs = moveAns.probabilities;\n"
            "  const rawChoice = moveAns.choice;\n",
            name,
        )
        assert "sandboxFallback" not in t and "0.25" not in t, name
    if name == "tetris.html":
        t = replace(t, '          <option value="heuristic">Heuristic (0 ms)</option>\n', "", name)
        t = replace(
            t,
            '  if (mode === "heuristic") {\n    return { ...heuristicDecision(placements), latencyMs: 0, live: true, placements };\n  }\n',
            "", name,
        )
        t = replace(t, "let sandboxFallback = false;\n", "", name)
        t = replace(t, "  if (!sandboxFallback) {\n    try {", "  {\n    try {", name)
        t = replace(
            t,
            """      setStatus(
        decision.live ? `${decision.latencyMs} ms · djev` : `${decision.latencyMs} ms · sim`,
        decision.live ? "live" : "warn"
      );
    } else {
      stats.lastLatency = 0;
      setStatus("0 ms · heuristic", "live");
    }""",
            """      setStatus(`${decision.latencyMs} ms · djev`, "live");
    }""",
            name,
        )
        t = replace(
            t,
            "  const chosen = byId.get(answer?.choice) || ranked[0]?.placement || placements[0];",
            '  const chosen = byId.get(answer?.choice);\n  if (!chosen) throw new Error("djev returned no placement");',
            name,
        )
        t = replace(
            t,
            "    ranked: ranked.length ? ranked : placements.slice(0, 6).map((p, i) => ({ placement: p, probability: i === 0 ? 0.5 : 0.1 })),\n"
            "    confidence: answer?.confidence ?? ranked[0]?.probability ?? 0.5,",
            "    ranked,\n    confidence: answer.confidence,",
            name,
        )
        t = replace(t, 'const topChoice = strat.choice || "build_clean";', "const topChoice = strat.choice;", name)
        t = replace(t, '"${answers?.strategy?.choice ?? "heuristic"}"', '"${answers?.strategy?.choice ?? "none"}"', name)
        t = replace(t, ".toFixed(2)) : 4.0;", ".toFixed(2)) : null;", name)
        t = replace(t, ".noul.toFixed(2)) : 1.0;", ".noul.toFixed(2)) : null;", name)
        t = replace(
            t,
            "  const initHeur = heuristicDecision(initialPlacements);\n"
            "  game.ghosts = [{ cells: initHeur.chosen.cells, probability: 1, chosen: true }];\n"
            '  renderProbabilityPanel({ ...initHeur, placements: initialPlacements, latencyMs: 0 }, "heuristic");\n',
            "  game.ghosts = [];\n"
            '  $("choice-summary").textContent = "waiting for djev";\n'
            "  for (let i = 0; i < 6; i++) {\n"
            '    $(`pslot-${i}`).textContent = "–"; $(`pdesc-${i}`).textContent = "–";\n'
            '    $(`pbar-${i}`).style.width = "0%"; $(`pval-${i}`).textContent = "–";\n'
            "  }\n"
            '  $("live-json").textContent = "No call yet. Press Start Autopilot.";\n',
            name,
        )
        # static placeholders that looked like model output before the first call
        for a, b in [
            ('<span id="choice-summary" class="panel-sub">rot 0, col 4 (0.0%)</span>', '<span id="choice-summary" class="panel-sub">waiting for djev</span>'),
            ('<span id="read-strat-val" class="read-val">build_clean</span>', '<span id="read-strat-val" class="read-val">–</span>'),
            ('<div id="read-strat-bar" class="bar-fill chosen" style="width: 50%"></div>', '<div id="read-strat-bar" class="bar-fill chosen" style="width: 0%"></div>'),
            ('<span id="read-strat-pct" class="prob-pct">50.0%</span>', '<span id="read-strat-pct" class="prob-pct">–</span>'),
            ('<span id="read-health-val" class="read-val">Clean (1.00)</span>', '<span id="read-health-val" class="read-val">–</span>'),
            ('<div id="read-health-bar" class="bar-fill good" style="width: 95%"></div>', '<div id="read-health-bar" class="bar-fill good" style="width: 0%"></div>'),
            ('<span id="read-health-score" class="prob-pct">1.0 / 4</span>', '<span id="read-health-score" class="prob-pct">–</span>'),
            ('<span id="read-fit-val" class="read-val">YES (clean spot)</span>', '<span id="read-fit-val" class="read-val">–</span>'),
            ('<div id="read-fit-bar" class="bar-fill good" style="width: 88%"></div>', '<div id="read-fit-bar" class="bar-fill good" style="width: 0%"></div>'),
            ('<span id="read-fit-pct" class="prob-pct">88.0%</span>', '<span id="read-fit-pct" class="prob-pct">–</span>'),
        ]:
            t = replace(t, a, b, name)
        assert "sandboxFallback" not in t and '"heuristic"' not in t and "· sim" not in t, name

    assert "simulateSystemOne" not in t, name
    assert "prompts: [baseStr" not in t, name
    assert "jev-latest" not in t, name

    # 6. UI only: shared nav bar + the Lab's minimal look (white, ink, greys, one NVIDIA green, Inter)
    t = restyle(name, t)
    (dst / name).write_text(t)
    print(f"patched {name}")

server = (src / "server.py").read_text()
server = replace(server, '"/tetris": "tetris.html", "/tetris.html": "tetris.html",',
                 '"/tetris": "tetris.html", "/tetris.html": "tetris.html",\n            "/lab": "lab.html", "/lab.html": "lab.html",', "server.py")
server = replace(server, 'data=json.dumps({"prompt": base_str, "add_special_tokens": False})',
                 'data=json.dumps({"prompt": THINK + base_str, "add_special_tokens": False})', "server.py")
server = replace(server, 'data=json.dumps({"prompt": alt_str, "add_special_tokens": False})',
                 'data=json.dumps({"prompt": THINK + alt_str, "add_special_tokens": False})', "server.py")
server = replace(server, "_TOK_CACHE = {}\n",
                 "_TOK_CACHE = {}\n# Empty thinking section the model expects before answering; without it the first answer slot\n"
                 "# sometimes holds neither label in its top-20 and the answer collapses to 50/50.\nTHINK = \"<|channel>thought\\n<channel|>\"\n", "server.py")
server = replace(server, '"diagnostics": {"timing": {"total_ms": total_ms}}}',
                 '"diagnostics": {"timing": {"total_ms": total_ms}, "request_id": out.get("id"), "served_by": "vllm djev-dgemma"}}', "server.py")
# security: the bare link opens the Lab, and errors no longer reveal internal details
server = replace(server, '"/": "snake.html", "/snake"', '"/": "lab.html", "/snake"', "server.py")
server = replace(server, '{"error": f"tokenize error {str(e)}"}', '{"error": "the tokenizer is not available"}', "server.py")
server = replace(server, '{"error": f"vllm timeout {str(e)}"}', '{"error": "the model did not answer in time"}', "server.py")
server = replace(server, "return await HTMLResponse(content=str(e), status_code=500)(scope, receive, send)",
                 'return await HTMLResponse(content="page not available", status_code=500)(scope, receive, send)', "server.py")
# security: every request from outside the container goes through the public gateway in lab/gateway.py
server = replace(server, "class SystemOneMiddleware:\n", (HERE / "lab" / "gateway.py").read_text() + "\n\nclass SystemOneMiddleware:\n", "server.py")
server = replace(server, "    async def __call__(self, scope, receive, send):\n", '''    _inflight = 0

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
        body = ("\\n".join(l for l in text.splitlines() if l.startswith(_METRIC_KEEP)) + "\\n").encode()
        await send({"type": "http.response.start", "status": status[0],
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    async def _handle(self, scope, receive, send):
''', "server.py")
(dst / "server.py").write_text(server)
print("patched server.py")

build_lab(dst)
