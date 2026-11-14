// Record a GIF of the live djev site with headless Chrome over the DevTools protocol.
// usage: DJEV_URL=https://your-djev-url node --experimental-websocket tools/record-gif.mjs <scenario> <out.gif>
// scenarios: tour, chat, batch, snake, tetris, dino
import { spawn, execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = (process.env.DJEV_URL || "").replace(/\/+$/, "");
if (!BASE) { console.error("Set DJEV_URL to your djev server, for example DJEV_URL=https://djev-lab-xxxx.run.app"); process.exit(2); }
const [scenarioName, outGif] = process.argv.slice(2);
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const W = 1280, H = 800, PORT = 9333 + Math.floor(Math.random() * 400);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// helpers available inside the page for every scenario
const PAGE_HELPERS = `
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function typeInto(sel, text, gap = 55) {
    const n = document.querySelector(sel); n.focus();
    for (const ch of text) { n.value += ch; n.dispatchEvent(new Event("input", { bubbles: true })); await sleep(gap); }
  }
  const tourNextBtn = () => document.querySelector("#tour-card .tour-next");
`;
const MOD_QUESTIONS = JSON.stringify([
  { id: "action", type: "choice", instructions: "What should the moderation bot do with this chat message?",
    options: [{ name: "allow", desc: "Normal chat, show it" }, { name: "hide", desc: "Spam, scams or rude messages" }, { name: "escalate", desc: "Someone may be at risk, a person should look now" }] },
  { id: "is_toxic", type: "noul", instructions: "Is this message insulting or hostile toward someone?", options: [] },
]);
const SEEN_TOUR = `localStorage.setItem("djev-lab-tour-done-v1", "1");`;

const SCENARIOS = {
  tour: {
    path: "/lab", prep: "", settle: 1500,
    run: `
      await sleep(1600);
      tourNextBtn().click(); await sleep(1700);
      await typeInto("#q-id", "action"); await sleep(700);
      tourNextBtn().click(); await sleep(1700);
      tourNextBtn().click(); await sleep(1300);
      await typeInto("#q-ins", "What should the moderation bot do with this chat message?", 35); await sleep(700);
      tourNextBtn().click(); await sleep(1500);
      document.querySelector("#tour-card .tour-fill").click(); await sleep(1700);
      tourNextBtn().click(); await sleep(2000);
      document.querySelectorAll("#chat-samples .chip")[1].click();
      await sleep(5200);
      tourNextBtn().click(); await sleep(2600);
      tourNextBtn().click(); await sleep(2600);`,
  },
  chat: {
    path: "/lab",
    prep: `${SEEN_TOUR}
      localStorage.setItem("djev-lab-progress-v1", JSON.stringify({ unlocked: [1, 2, 3, 4], ran: true, badges: [], presetsRun: [] }));
      localStorage.setItem("djev-lab-draft-v1", JSON.stringify({ v: 1, preset: "moderation", state: "", batch: "", questions: ${MOD_QUESTIONS} }));`,
    afterLoad: `go(3); await sleep(300);
      const navH = document.querySelector(".djnav").offsetHeight;
      window.scrollTo(0, document.getElementById("chat").getBoundingClientRect().top + scrollY - navH - 90);`,
    settle: 900,
    run: `
      await sleep(900);
      document.getElementById("chat-play").click();
      await sleep(6800);
      await typeInto("#chat-input", "you're all legends, gg everyone", 45);
      document.getElementById("chat-form").requestSubmit();
      await sleep(3000);
      document.querySelector("#chat-log .msg.hidden-msg .msg-text").click();
      await sleep(2200);`,
  },
  batch: {
    path: "/lab",
    prep: `${SEEN_TOUR}
      localStorage.setItem("djev-lab-progress-v1", JSON.stringify({ unlocked: [1, 2, 3, 4], ran: true, badges: ["first"], presetsRun: ["firewall"] }));`,
    afterLoad: `loadPreset("firewall"); go(4); await sleep(300); window.scrollTo(0, 0);`,
    settle: 900,
    run: `
      await sleep(1000);
      document.getElementById("batch-run").click();
      await sleep(2600);
      const navH = document.querySelector(".djnav").offsetHeight;
      window.scrollTo({ top: document.getElementById("stats").getBoundingClientRect().top + scrollY - navH - 40, behavior: "smooth" });
      await sleep(3200);`,
  },
  snake: { path: "/snake", prep: SEEN_TOUR, settle: 900,
    run: `await sleep(800); document.getElementById("btn-start").click(); await sleep(9000);` },
  tetris: { path: "/tetris", prep: SEEN_TOUR, settle: 900,
    run: `await sleep(800); [...document.querySelectorAll("button")].find((b) => /Start Autopilot/.test(b.textContent)).click(); await sleep(10000);` },
  dino: { path: "/dino", prep: SEEN_TOUR, settle: 900,
    run: `await sleep(600);
      const sel = [...document.querySelectorAll("select")].find((s) => [...s.options].some((o) => /Auto-restart/i.test(o.text)));
      sel.value = [...sel.options].find((o) => /Auto-restart/i.test(o.text)).value; sel.dispatchEvent(new Event("change", { bubbles: true }));
      [...document.querySelectorAll("button")].find((b) => /Autopilot/.test(b.textContent)).click(); await sleep(9000);` },
};

const sc = SCENARIOS[scenarioName];
if (!sc) { console.error("unknown scenario", scenarioName); process.exit(2); }

const profile = mkdtempSync(join(tmpdir(), "djev-gif-"));
const frameDir = mkdtempSync(join(tmpdir(), "djev-frames-"));
const chrome = spawn(CHROME, [
  "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, `--window-size=${W},${H}`,
  "--hide-scrollbars", "--mute-audio", "--no-first-run", "--no-default-browser-check", "--disable-extensions", "about:blank",
], { stdio: "ignore" });

let ws, id = 0;
const pending = new Map(), listeners = [];
function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const msgId = ++id; pending.set(msgId, { resolve, reject });
    ws.send(JSON.stringify({ id: msgId, method, params }));
  });
}
async function evaluate(expr) {
  const r = await send("Runtime.evaluate", { expression: `(async () => { ${PAGE_HELPERS} ${expr} })()`, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 600));
  return r.result && r.result.value;
}
async function loadAndWait(url) {
  const loaded = new Promise((res) => listeners.push((m) => { if (m.method === "Page.loadEventFired") { res(); return true; } }));
  await send("Page.navigate", { url });
  await loaded;
}

try {
  let target;
  for (let i = 0; i < 50 && !target; i++) {
    try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find((t) => t.type === "page"); } catch { /* not up yet */ }
    if (!target) await sleep(200);
  }
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r, { once: true }));
  const frames = [];
  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result); return; }
    if (m.method === "Page.screencastFrame") {
      frames.push({ t: m.params.metadata.timestamp, data: m.params.data });
      send("Page.screencastFrameAck", { sessionId: m.params.sessionId }).catch(() => {});
      return;
    }
    for (let i = listeners.length - 1; i >= 0; i--) if (listeners[i](m)) listeners.splice(i, 1);
  });
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Network.setUserAgentOverride", { userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36" });
  await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: false });

  await loadAndWait(BASE + sc.path);
  if (sc.prep) { await evaluate(sc.prep); await loadAndWait(BASE + sc.path); }
  await sleep(sc.settle || 800);
  if (sc.afterLoad) await evaluate(sc.afterLoad);
  await sleep(500);

  await send("Page.startScreencast", { format: "jpeg", quality: 88, maxWidth: W, maxHeight: H, everyNthFrame: 1 });
  await evaluate(sc.run);
  await sleep(300);
  await send("Page.stopScreencast");

  // frames -> concat list with real timings -> GIF
  let list = "";
  frames.forEach((f, i) => {
    const name = `f${String(i).padStart(5, "0")}.jpg`;
    writeFileSync(join(frameDir, name), Buffer.from(f.data, "base64"));
    const dur = i + 1 < frames.length ? Math.max(0.02, frames[i + 1].t - f.t) : 1.2;
    list += `file '${join(frameDir, name)}'\nduration ${dur.toFixed(3)}\n`;
  });
  list += `file '${join(frameDir, `f${String(frames.length - 1).padStart(5, "0")}.jpg`)}'\n`;
  writeFileSync(join(frameDir, "list.txt"), list);
  mkdirSync(join(outGif, ".."), { recursive: true });
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", join(frameDir, "list.txt"),
    "-vf", "fps=12,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=160:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
    "-loop", "0", outGif]);
  console.log(`${scenarioName}: ${frames.length} frames -> ${outGif}`);
} finally {
  try { ws && ws.close(); } catch { /* ignore */ }
  chrome.kill("SIGKILL");
  await sleep(300);
  rmSync(profile, { recursive: true, force: true });
  rmSync(frameDir, { recursive: true, force: true });
}
