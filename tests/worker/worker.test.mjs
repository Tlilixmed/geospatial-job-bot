// Tests for cloudflare/worker.js with a fake R2 bucket, fake Telegram/GitHub and a fake Workers AI.
// Run: node --test tests/worker/worker.test.mjs
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const source = await readFile(new URL("../../cloudflare/worker.js", import.meta.url), "utf8");
const worker = (await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`)).default;

const HOUR = 36e5;
const iso = (hoursAgo) => new Date(Date.now() - hoursAgo * HOUR).toISOString();
const CODES = { "gh:acme:1": "419b7", "gh:acme:2": "2fd2a", "lever:old:9": "806ad",  // geojobbot.utils.text.job_code
  "gh:acme:3": "33338", "gh:acme:4": "a518c" };

function job(id, overrides = {}) {
  return { id, code: CODES[id], t: "GIS Analyst", c: "Acme", loc: "Toronto, Canada", country: "Canada", s: 82, tier: "high",
    p: iso(24), rel: true, seen: iso(2), rot: false, url: `https://acme.example/${id}`, sal: null, sk: ["ArcGIS Pro (required)", "Python"],
    dom: ["GIS"], why: ["Title: GIS Analyst"], bd: { title: 40, tech: 20, domain: 15, responsibilities: 5, location: 2 }, rej: [],
    offered: false, src: ["greenhouse:acme"], ...overrides };
}

function makeIndex(jobs) {
  return { schema: 1, generated_at: iso(1), help: "HELP TEXT /jobs", jobs, signals: [],
    settings: { high: 70, medium: 55, max_age_h: 360, rotation_max_age_h: 360, notify_possible: false, exclude_internships: true, ai: true },
    run: { started_at: iso(1), code: "abc1234", high: 1, possible: 0, alerted: 1, new: 1, failed: [], jobs_in_state: 3 } };
}

function harness({ index, prefs, ai, inbox = true, prefix = "", telegramAnswer } = {}) {
  const objects = new Map();
  const uploaded = new Map();  // what R2's head() reports; an index starts out uploaded when it says it was generated
  if (index) {
    objects.set(`${prefix}state/index.json`, JSON.stringify(index));
    uploaded.set(`${prefix}state/index.json`, new Date(index.generated_at));
  }
  if (prefs) objects.set(`${prefix}state/prefs.json`, JSON.stringify(prefs));
  const sent = [];  // sendMessage bodies
  const calls = [];  // every Bot API call: { method, body }
  const dispatched = [];
  const requests = [];  // "METHOD url" of every GitHub request
  const runs = { queued: 0, in_progress: 0 };  // scraper runs GitHub reports
  const github = { dispatchStatus: 204 };
  const reads = [];
  const writes = [];
  const env = { TELEGRAM_BOT_TOKEN: "T", TELEGRAM_CHAT_ID: "42", WEBHOOK_SECRET: "s3cret", GITHUB_TOKEN: "gh", GITHUB_REPO: "o/r" };
  if (inbox) {
    env.INBOX = {
      get: async (key) => { reads.push(key); return objects.has(key) ? { json: async () => JSON.parse(objects.get(key)) } : null; },
      head: async (key) => (objects.has(key) ? { uploaded: uploaded.get(key) || new Date() } : null),
      put: async (key, value) => { writes.push(key); objects.set(key, value); uploaded.set(key, new Date()); },
    };
  }
  if (prefix) env.STATE_PREFIX = prefix;
  if (ai) env.AI = { run: async () => ({ response: ai }) };
  globalThis.fetch = async (url, init) => {
    const body = init.body ? JSON.parse(init.body) : null;
    if (String(url).includes("api.telegram.org")) {
      const method = String(url).split("/").pop();
      calls.push({ method, body });
      if (method === "sendMessage") sent.push(body);
      const answer = telegramAnswer && telegramAnswer(method, body, calls);
      if (answer) return { ok: answer.status === 200, status: answer.status, json: async () => answer.body };
      return { ok: true, status: 200 };
    }
    requests.push(`${init.method} ${url}`);
    if (body) dispatched.push(body);  // PUT …/enable and GET …/runs have no body
    const asked = String(url).match(/[?&]status=(\w+)/);
    if (asked) return { ok: true, status: 200, json: async () => ({ total_count: runs[asked[1]], workflow_runs: [] }) };
    const status = String(url).endsWith("/dispatches") ? github.dispatchStatus : 204;
    return { ok: status < 300, status };
  };
  /** The index as R2's head() reports it: uploaded `hours` ago. */
  const indexAge = (hours) => {
    if (!objects.has(`${prefix}state/index.json`)) objects.set(`${prefix}state/index.json`, JSON.stringify(makeIndex([])));
    uploaded.set(`${prefix}state/index.json`, new Date(Date.now() - hours * HOUR));
  };
  let updateId = 100;
  async function say(text, { chat = 42, from = 42, secret = "s3cret" } = {}) {
    const pending = [];
    const request = new Request("https://worker.example/", {
      method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": secret },
      body: JSON.stringify({ update_id: updateId++, message: { text, chat: { id: chat, type: "private" }, from: { id: from, is_bot: false } } }),
    });
    const response = await worker.fetch(request, env, { waitUntil: (p) => pending.push(p) });
    await Promise.all(pending);
    return response;
  }
  /** A tap on an inline button of a message the bot sent; date 0 is an InaccessibleMessage (too old to read). */
  async function tap(data, { keyboard = [], from = 42, chat = 42, date = 1700000000, message = true } = {}) {
    const pending = [];
    const cq = { id: `cq${updateId}`, from: { id: from, is_bot: false }, chat_instance: "1", data };
    if (message) cq.message = { message_id: 7, date, chat: { id: chat, type: "private" }, from: { id: 1, is_bot: true },
      ...(date ? { text: "digest", reply_markup: { inline_keyboard: keyboard } } : {}) };
    const request = new Request("https://worker.example/", {
      method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": "s3cret" },
      body: JSON.stringify({ update_id: updateId++, callback_query: cq }),
    });
    const response = await worker.fetch(request, env, { waitUntil: (p) => pending.push(p) });
    await Promise.all(pending);
    return response;
  }
  const stored = (key) => (objects.has(key) ? JSON.parse(objects.get(key)) : null);
  const tick = async () => { const pending = []; await worker.scheduled({}, env, { waitUntil: (x) => pending.push(x) }); await Promise.all(pending); };
  const get = (path) => worker.fetch(new Request("https://worker.example" + path), env, { waitUntil() {} });
  const of = (method) => calls.filter((c) => c.method === method).map((c) => c.body);
  return { say, tap, get, tick, env, sent, calls, of, reads, writes, dispatched, stored, objects, requests, runs, github, indexAge };
}

test("/jobs is answered from the index without starting a workflow", async () => {
  const h = harness({ index: makeIndex([job("gh:acme:1"), job("gh:acme:2", { t: "LiDAR Technician", c: "ScanCo", s: 61, tier: "possible",
    ai: { fit: 7, summary: "Good LiDAR fit", concerns: "Needs a licence" } })]) });
  await h.say("/jobs 5");
  assert.equal(h.dispatched.length, 0);
  assert.equal(h.sent.length, 1);
  const text = h.sent[0].text;
  assert.equal(h.sent[0].parse_mode, "HTML");
  assert.match(text, /Top 2 of 2 current matches/);
  assert.match(text, /<b>GIS Analyst<\/b> — Acme · 82\/100 · <code>419b7<\/code>/);
  assert.match(text, /💡 Good LiDAR fit/);
  assert.ok(text.indexOf("High matches") < text.indexOf("Possible and near misses"));
  await h.say("/high");
  assert.match(h.sent[1].text, /Top 1 of 1 High matches/);
});

test("stale, hidden, applied and muted jobs are left out, like in Python", async () => {
  const jobs = [job("gh:acme:1", { p: iso(24 * 30) }), job("gh:acme:2", { seen: iso(24 * 8) }),
    job("lever:old:9", { c: "Leidos" })];
  const h = harness({ index: makeIndex(jobs), prefs: { muted: ["leidos"] } });
  await h.say("/jobs");
  assert.match(h.sent[0].text, /Nothing relevant and fresh/);
});

test("search and range ignore accents and honour a STATE_PREFIX", async () => {
  const h = harness({ index: makeIndex([job("gh:acme:1", { t: "Technicien en géomatique", loc: "Montréal, Canada", s: 64, tier: "possible" })]) });
  await h.say("/search geomatique montreal");
  assert.match(h.sent[0].text, /1 stored match for “geomatique montreal”/);
  await h.say("/range 60 70");
  assert.match(h.sent[1].text, /1 job scoring 60–70/);
  await h.say("/range 80");
  assert.match(h.sent[2].text, /No fresh job scores between 80 and 100/);

  const prefixed = harness({ index: makeIndex([job("gh:acme:1")]), prefix: "bots/geo/" });
  await prefixed.say("/hide 419b7");
  assert.deepEqual(prefixed.stored("bots/geo/state/prefs.json").hidden, ["gh:acme:1"]);
  await prefixed.say("/run");
  assert.ok(prefixed.objects.has("bots/geo/inbox/000000000101.json"));
});

test("plain words work, with an echo of what was understood", async () => {
  const h = harness({ index: makeIndex([job("gh:acme:1")]) });
  await h.say("Status");
  assert.match(h.sent[0].text, /^↪ <i>\/status<\/i>/);
  assert.match(h.sent[0].text, /fresh matches now: 1/);
  await h.say("top 3");
  assert.match(h.sent[1].text, /↪ <i>\/jobs 3<\/i>/);
  await h.say("419b7");
  assert.match(h.sent[2].text, /Score 82\/100 · high/);
  await h.say("/help");
  assert.equal(h.sent[3].text, "HELP TEXT /jobs");
  assert.equal(h.dispatched.length, 0);
  const get = h.env.INBOX.get;
  const reads = [];
  h.env.INBOX.get = async (key) => { reads.push(key); return get(key); };
  await h.say("top 3");
  assert.equal(reads.filter((key) => key === "state/index.json").length, 1);  // read and parsed once for a shortcut
});

test("/hide, /applied and /outcome write the same prefs schema Python uses", async () => {
  const h = harness({ index: makeIndex([job("gh:acme:1"), job("gh:acme:2", { t: "Remote Sensing Analyst" })]) });
  await h.say("/hide 2fd2a");
  let prefs = h.stored("state/prefs.json");
  assert.deepEqual(prefs.hidden, ["gh:acme:2"]);
  assert.equal(prefs.hidden_info["gh:acme:2"].title, "Remote Sensing Analyst");
  assert.deepEqual(prefs.hidden_info["gh:acme:2"].skills, ["ArcGIS Pro", "Python"]);
  await h.say("/applied 419b7");
  await h.say("/outcome 419b7 interview");
  prefs = h.stored("state/prefs.json");
  const application = prefs.applied["gh:acme:1"];
  assert.equal(application.status, "interview");
  assert.deepEqual(application.history.map((e) => e.status), ["applied", "interview"]);
  assert.equal(application.url, "https://acme.example/gh:acme:1");
  assert.ok(prefs.updated_at && prefs.paused === false && prefs.learning === null);
  await h.say("/applied");
  assert.match(h.sent.at(-1).text, /🎤 GIS Analyst — Acme · interview · .* · <code>419b7<\/code>/);  // code derived by SHA-1, as in Python
  await h.say("/jobs");
  assert.match(h.sent.at(-1).text, /Nothing relevant and fresh/);
  await h.say("/unhide 2fd2a");
  assert.deepEqual(h.stored("state/prefs.json").hidden, []);
  assert.equal(h.dispatched.length, 1);  // only the interview sheet went to Python
});

test("an outcome can be recorded for an application that left the index", async () => {
  const h = harness({ index: makeIndex([]), prefs: { applied: { "lever:old:9": { title: "Cartographer", at: iso(500), status: "applied" } } } });
  await h.say("/outcome 806ad rejected");
  assert.equal(h.stored("state/prefs.json").applied["lever:old:9"].status, "rejected");
  assert.match(h.sent[0].text, /Noted\. Their loss/);
});

test("settings commands", async () => {
  const h = harness({ index: makeIndex([job("gh:acme:1")]) });
  await h.say("/threshold 75 60");
  await h.say("/mute Leidos");
  await h.say("/possible on");
  await h.say("/pause");
  const prefs = h.stored("state/prefs.json");
  assert.deepEqual([prefs.high_threshold, prefs.medium_threshold, prefs.muted, prefs.notify_possible, prefs.paused], [75, 60, ["Leidos"], true, true]);
  await h.say("/interns maybe");  // not understood here: Python explains the usage
  assert.equal(h.dispatched.length, 1);
});

test("slow commands and a missing index go to the Python workflow, privately", async () => {
  const h = harness({ index: makeIndex([job("gh:acme:1")]) });
  await h.say("/pitch 419b7");
  assert.match(h.sent[0].text, /On it/);
  assert.deepEqual(h.dispatched[0], { ref: "main", inputs: { inbox: "true" } });
  assert.equal(h.stored("inbox/000000000100.json").text, "/pitch 419b7");

  const empty = harness({});
  await empty.say("/jobs");
  assert.equal(empty.dispatched.length, 1);

  const unbound = harness({ inbox: false });
  await unbound.say("/status");
  assert.deepEqual(unbound.dispatched[0].inputs, { text: "/status", hint: "" });
});

test("the AI may open read-only views by itself, but never change a setting", async () => {
  const index = makeIndex([job("gh:acme:1", { sp: [{ label: "UK sponsor register", icon: "🇬🇧", country: "United Kingdom", name: "ACME LTD", match: "exact" }] })]);
  const reader = harness({ index, ai: "/sponsors" });
  await reader.say("which of these employers could get me a visa?");
  assert.equal(reader.dispatched.length, 0);
  assert.match(reader.sent[0].text, /^↪ <i>\/sponsors · AI<\/i>/);
  assert.match(reader.sent[0].text, /UK sponsor register: ACME LTD/);

  const writer = harness({ index, ai: "/pause" });
  await writer.say("please stop bothering me for a while");
  assert.equal(writer.stored("state/prefs.json"), null);
  assert.equal(writer.dispatched.length, 1);
  assert.equal(writer.stored("inbox/000000000100.json").hint, "/pause");  // the Python rules decide what to do with it

  const nonsense = harness({ index, ai: "Sure! I think you want /jobs" });
  await nonsense.say("hmm what about that thing");
  assert.equal(nonsense.stored("inbox/000000000100.json").hint, "");
});

test("views formatted by Python are sent as they are; a missing view goes to Python", async () => {
  const index = makeIndex([]);
  index.views = { sources: "📊 <b>Source yield</b>" };
  const h = harness({ index });
  await h.say("/yield");
  await h.say("sources");
  assert.equal(h.sent[0].text, "📊 <b>Source yield</b>");
  assert.match(h.sent[1].text, /Source yield/);
  assert.equal(h.dispatched.length, 0);
  const older = harness({ index: makeIndex([]) });
  await older.say("/sources");
  assert.equal(older.dispatched.length, 1);
});

test("visa routes: badge on the job, details in /why, list, country card", async () => {
  const index = makeIndex([job("gh:acme:1", { visa: { v: "strong", b: "🟢 Visa route looks open — Skilled Worker visa",
    d: ["🟢 <b>Visa route: Skilled Worker visa</b> — looks open", "   ✓ salary £45,000 ≥ £41,700/year"] } })]);
  index.views = { visa: "🛂 <b>Visa routes of current matches</b>" };
  index.visa_cards = { uk: "🛂 <b>Work-visa routes: United Kingdom</b>" };
  const h = harness({ index });
  await h.say("/jobs");
  assert.match(h.sent[0].text, /🟢 Visa route looks open — Skilled Worker visa/);
  await h.say("/why 419b7");
  assert.match(h.sent[1].text, /✓ salary £45,000 ≥ £41,700\/year/);
  await h.say("visa");
  assert.match(h.sent[2].text, /Visa routes of current matches/);
  await h.say("/visa UK");
  assert.match(h.sent[3].text, /Work-visa routes: United Kingdom/);
  await h.say("/visa 419b7");
  assert.match(h.sent[4].text, /^🟢 <b>Visa route: Skilled Worker visa/);
  assert.equal(h.dispatched.length, 0);
  await h.say("/visa atlantis");
  assert.equal(h.dispatched.length, 1);
});

test("saving preferences keeps keys the Worker does not know", async () => {
  const h = harness({ index: makeIndex([job("gh:acme:1")]), prefs: { watch: [{ name: "Fugro" }], future_key: 7 } });
  await h.say("/pause");
  const prefs = h.stored("state/prefs.json");
  assert.deepEqual(prefs.watch, [{ name: "Fugro" }]);
  assert.equal(prefs.future_key, 7);
  assert.equal(prefs.paused, true);
  await h.say("/watch Hexagon");  // decided by Python
  assert.equal(h.dispatched.length, 1);
});

test("recording an interview answers at once and asks Python for the interview sheet", async () => {
  const h = harness({ index: makeIndex([job("gh:acme:1")]) });
  await h.say("/outcome 419b7 interview");
  assert.match(h.sent[0].text, /An interview!/);
  assert.match(h.sent[1].text, /Preparing your interview sheet/);
  assert.equal(h.stored("inbox/000000000100.json").text, "/prep 419b7");
  assert.equal(h.dispatched.length, 1);
  await h.say("/outcome 419b7 rejected");
  assert.equal(h.dispatched.length, 1);
});

test("strangers and wrong secrets are refused", async () => {
  const h = harness({ index: makeIndex([job("gh:acme:1")]) });
  assert.equal((await h.say("/jobs", { secret: "nope" })).status, 403);
  assert.equal(await (await h.say("/jobs", { chat: 7, from: 7 })).text(), "ignored");
  assert.equal(h.sent.length, 0);
  await h.say("/jobs", { chat: -100123, from: 42 });  // the owner, writing from a group
  assert.equal(h.sent.length, 1);
  assert.equal(h.sent[0].chat_id, "42");
});

test("the dashboard is off without a key, private with one, and cannot be broken out of", async () => {
  const evil = job("gh:acme:1", { t: "</script><script>alert(1)</script>" });
  const h = harness({ index: makeIndex([evil]), prefs: { applied: { x: { title: "Cartographer", status: "interview" } } } });
  assert.equal((await h.get("/dash/anything")).status, 404);  // no DASHBOARD_KEY: disabled
  h.env.DASHBOARD_KEY = "k".repeat(24);
  assert.equal((await h.get("/dash/wrong-key-wrong-key-wrong")).status, 404);
  assert.equal(await (await h.get("/")).text(), "geospatial job bot webhook");
  const page = await h.get("/dash/" + "k".repeat(24));
  assert.equal(page.status, 200);
  assert.equal(page.headers.get("Cache-Control"), "no-store");
  assert.match(page.headers.get("Content-Security-Policy"), /default-src 'none'/);
  const html = await page.text();
  assert.equal(html.split("</script>").length, 3);  // the data block and the page script, nothing injected
  const data = JSON.parse(html.split('type="application/json">')[1].split("</script>")[0]);
  assert.equal(data.index.jobs[0].t, "</script><script>alert(1)</script>");
  assert.equal(data.prefs.applied.x.status, "interview");
  assert.equal(data.prefs.muted, undefined);  // only what the page needs
  const script = html.split("<script>")[1].split("</script>")[0];
  assert.doesNotThrow(() => new Function(script));  // the inline script parses
});

test("filler: an index 1.75 h old starts the scraper, unless a run is queued or in progress", async () => {
  const fresh = harness({ index: { ...makeIndex([]), generated_at: iso(9) } });
  fresh.indexAge(0.5);  // its upload time counts, not what it says inside
  await fresh.tick();
  assert.equal(fresh.sent.length + fresh.requests.length, 0);
  assert.equal(fresh.stored("state/watchdog.json"), null);  // nothing changed: no R2 write either

  const h = harness({ index: makeIndex([]) });
  h.indexAge(1.5);
  await h.tick();
  assert.equal(h.requests.length, 0);
  h.indexAge(2);
  for (const busy of ["queued", "in_progress"]) {
    h.runs[busy] = 1;
    await h.tick();
    assert.equal(h.dispatched.length, 0, busy);
    h.runs[busy] = 0;
  }
  assert.ok(h.requests.includes("GET https://api.github.com/repos/o/r/actions/workflows/scraper.yml/runs?status=queued&per_page=1"));
  assert.ok(h.requests.includes("GET https://api.github.com/repos/o/r/actions/workflows/scraper.yml/runs?status=in_progress&per_page=1"));
  await h.tick();
  assert.deepEqual(h.dispatched, [{ ref: "main" }]);
  assert.ok(h.requests.includes("PUT https://api.github.com/repos/o/r/actions/workflows/scraper.yml/enable"));  // the re-enable stays
  assert.equal(h.sent.length, 0);  // filling a gap is routine: nothing is said
  assert.equal(h.stored("state/watchdog.json").kicks, 1);
  await h.tick();  // the run has not written the index yet: no second run within the hour
  assert.equal(h.dispatched.length, 1);
  h.objects.set("state/watchdog.json", JSON.stringify({ ...h.stored("state/watchdog.json"), last_kick: iso(0.5) }));
  h.indexAge(0.2);  // the run wrote the index a while after it was started
  await h.tick();
  assert.equal(h.stored("state/watchdog.json").kicks, 0);
  assert.equal(h.sent.length, 0);
});

test("watchdog: a scraper that crashes is started again after 1, 2 and 4 hours, then left alone; one ring, then recovery", async () => {
  const h = harness({ index: makeIndex([]) });
  const lastKick = (hoursAgo) => {  // let time pass since the last run the Worker started
    const memo = h.stored("state/watchdog.json");
    h.objects.set("state/watchdog.json", JSON.stringify({ ...memo, last_kick: iso(hoursAgo) }));
  };
  const steps = [  // [index age, hours since the last kick (null: as it is), runs started so far, rings so far]
    [2, null, 1, 0],
    [3, 0.5, 1, 0],    // too soon
    [3, 1, 2, 0],      // 1 hour after the first
    [4, 1.5, 2, 0],    // 2 hours are due after the second
    [5, 2, 3, 0],
    [6.5, 1, 3, 1],    // six hours of silence ring once, while waiting
    [8, 3, 3, 1],      // 4 hours are due after the third
    [9, 4, 4, 2],      // the fourth and last: said once more
    [12, 8, 4, 2],     // given up: nothing more is started or said
    [20, 16, 4, 2],
  ];
  for (const [age, since, started, rings] of steps) {
    h.indexAge(age);
    if (since !== null) lastKick(since);
    await h.tick();
    assert.equal(h.dispatched.length, started, `age ${age}`);
    assert.equal(h.sent.length, rings, `age ${age}`);
  }
  assert.match(h.sent[0].text, /^⚠️ No scraper run for 7 hours\. The run I started did not finish/);
  assert.match(h.sent[1].text, /Still no scraper run after 4 attempts/);
  for (const ringing of h.sent) assert.ok(ringing.parse_mode === "HTML" && !ringing.disable_notification);  // these ring
  h.indexAge(0.1);  // someone fixed it: a run wrote the index
  await h.tick();
  assert.match(h.sent[2].text, /running again/);
  assert.equal(h.sent[2].disable_notification, true);  // good news is quiet
  assert.deepEqual({ ...h.stored("state/watchdog.json"), last_kick: undefined }, { alerted: false, kicks: 0, last_kick: undefined });
  h.indexAge(2);
  await h.tick();  // and the filler works again at once
  assert.equal(h.dispatched.length, 5);
});

test("watchdog: no index at all, and a dispatch GitHub refuses, ring at once", async () => {
  const h = harness({});
  h.github.dispatchStatus = 403;
  await h.tick();
  assert.equal(h.sent.length, 1);
  assert.match(h.sent[0].text, /^⚠️ No scraper run for a long time\. GitHub refused to start the scraper \(HTTP 403\)\. Check the Worker's GITHUB_TOKEN/);
  assert.equal(h.stored("state/watchdog.json").refused, 403);
  const ok = harness({});
  await ok.tick();
  assert.match(ok.sent[0].text, /^🛟 No scraper run for a long time\. I re-enabled the schedule and started a run myself\./);
});

test("watchdog: a token GitHub refuses rings at once, is named again at six hours, and never exhausts the starts", async () => {
  const h = harness({ index: makeIndex([]) });
  h.github.dispatchStatus = 401;  // an expired fine-grained token
  const tries = () => h.dispatched.length;
  const steps = [  // [index age, dispatches tried so far, rings so far]
    [2, 1, 1],     // the first refusal rings at once, with the cause
    [3, 2, 1],     // tried again each hour (nothing ran, nothing to wait for), but not said again
    [4, 3, 1],
    [5, 4, 1],
    [6.5, 5, 2],   // six hours of silence: the alert names the refusal, not a run that "did not finish"
    [8, 6, 2],
    [12, 7, 2],    // more than MAX_KICKS refusals: still trying, since none of them counted
  ];
  for (const [age, tried, rings] of steps) {
    h.indexAge(age);
    await h.tick();
    assert.equal(tries(), tried, `age ${age}`);
    assert.equal(h.sent.length, rings, `age ${age}`);
  }
  assert.match(h.sent[0].text, /^⚠️ GitHub refused to start the scraper \(HTTP 401\)\. Check the Worker's GITHUB_TOKEN/);
  assert.match(h.sent[1].text, /^⚠️ No scraper run for 7 hours\. GitHub refused to start the scraper \(HTTP 401\)/);
  for (const ringing of h.sent) assert.ok(ringing.parse_mode === "HTML" && !ringing.disable_notification);
  assert.equal(h.stored("state/watchdog.json").kicks || 0, 0);
  h.github.dispatchStatus = 204;  // the token was replaced: the filler recovers by itself
  await h.tick();
  assert.equal(tries(), 8);
  assert.match(h.sent[2].text, /GitHub accepts my scraper starts again/);
  assert.equal(h.sent[2].disable_notification, true);
  const memo = h.stored("state/watchdog.json");
  assert.equal(memo.kicks, 1);
  assert.equal(memo.refused, undefined);
  h.indexAge(0.1);
  await h.tick();
  assert.match(h.sent[3].text, /running again/);
});

function mail({ from, subject, body }) {
  const boundary = "b0undary";
  const raw = [`From: ${from}`, "To: jobs@example.test", `Subject: ${subject}`, `Content-Type: multipart/alternative; boundary="${boundary}"`, "",
    `--${boundary}`, "Content-Type: text/plain; charset=utf-8", "Content-Transfer-Encoding: quoted-printable", "", body.replace(/é/g, "=C3=A9").replace(/à/g, "=C3=A0"),
    `--${boundary}`, "Content-Type: text/html; charset=utf-8", "", `<p>${body}</p>`, `--${boundary}--`, ""].join("\r\n");
  return { from, headers: new Map([["subject", subject]]), raw: new Response(raw).body };
}

test("email replies are matched to applications, classified and recorded", async () => {
  const applied = { "gh:acme:1": { title: "GIS Analyst", company: "Acme Geospatial Ltd", at: iso(100), status: "applied" },
    "lever:old:9": { title: "Cartographer", company: "MapCo", at: iso(200), status: "applied" } };
  const h = harness({ index: makeIndex([]), prefs: { applied } });
  const run = async (m) => { const pending = []; await worker.email(m, h.env, { waitUntil: (p) => pending.push(p) }); await Promise.all(pending); };
  await run(mail({ from: "Recruiting Team <talent@acmegeospatial.com>", subject: "Your application to Acme Geospatial",
    body: "Bonjour, malheureusement nous ne pouvons pas donner suite à votre candidature." }));
  let prefs = h.stored("state/prefs.json");
  assert.equal(prefs.applied["gh:acme:1"].status, "rejected");
  assert.equal(prefs.applied["gh:acme:1"].history.at(-1).source, "email");
  assert.match(h.sent[0].text, /reads like a rejection for <b>GIS Analyst<\/b> — Acme Geospatial Ltd — recorded as <b>rejected<\/b>/);
  assert.doesNotMatch(h.sent[0].text, /malheureusement/);  // the body never reaches the chat
  await run(mail({ from: "hr@mapco.example", subject: "Interview: Cartographer role",
    body: "We would like to invite you to an interview next week. What is your availability for a call?" }));
  prefs = h.stored("state/prefs.json");
  assert.equal(prefs.applied["lever:old:9"].status, "interview");
  assert.match(h.sent[1].text, /an interview invitation/);
  assert.ok([...h.objects.keys()].some((k) => k.startsWith("inbox/")));  // the interview sheet was requested from Python
  await run(mail({ from: "newsletter@unknown.example", subject: "Weekly digest", body: "Lots of jobs this week." }));
  assert.match(h.sent.at(-1).text, /is not one I can classify/);
  assert.equal(h.stored("state/prefs.json").applied["gh:acme:1"].status, "rejected");  // untouched
  await run(mail({ from: "jobs@acmegeospatial.com", subject: "Thank you for applying", body: "We have received your application and will be in touch." }));
  assert.match(h.sent.at(-1).text, /an acknowledgement.*probably about/);
  assert.equal(h.stored("state/prefs.json").applied["gh:acme:1"].status, "rejected");  // an acknowledgement changes nothing
});

// ---------------------------------------------------------------------------- review round (docs/REVIEW.md F58-F67)
test("muting matches whole words: US does not silence Industry", async () => {
  const jobs = [job("gh:acme:1", { c: "Geo Industry Partners" }), job("gh:acme:2", { t: "GIS Analyst (US)", c: "Other" })];
  const h = harness({ index: makeIndex(jobs), prefs: { muted: ["US"] } });
  await h.say("/jobs");
  assert.match(h.sent[0].text, /Top 1 of 1 current matches/);
  assert.match(h.sent[0].text, /Geo Industry Partners/);
});

test("saying /applied twice keeps the recorded outcome, and unknown codes go to Python", async () => {
  const h = harness({ index: makeIndex([job("gh:acme:1")]) });
  await h.say("/applied 419b7");
  await h.say("/outcome 419b7 interview");
  await h.say("/applied 419b7");
  assert.match(h.sent.at(-1).text, /Already recorded: <b>GIS Analyst<\/b> · interview/);
  assert.equal(h.stored("state/prefs.json").applied["gh:acme:1"].status, "interview");
  const before = h.dispatched.length;
  await h.say("/why 0abcd");  // not in the index (an older job): the full state is Python's
  assert.equal(h.dispatched.length, before + 1);
  await h.say("/outcome constructor 419b7");  // a prototype key is not a status
  assert.match(h.sent.at(-1).text, /Usage: \/outcome/);
});

test("a decimal threshold is one number, and an edited message never writes", async () => {
  const h = harness({ index: makeIndex([job("gh:acme:1")]) });
  await h.say("/threshold 75.5");
  let prefs = h.stored("state/prefs.json");
  assert.equal(prefs.high_threshold, 76);
  assert.equal(prefs.medium_threshold, 55);
  const edited = async (text) => {
    const pending = [];
    await worker.fetch(new Request("https://worker.example/", { method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": "s3cret" },
      body: JSON.stringify({ update_id: 900, edited_message: { text, chat: { id: 42, type: "private" }, from: { id: 42, is_bot: false } } }) }),
    h.env, { waitUntil: (p) => pending.push(p) });
    await Promise.all(pending);
  };
  await edited("/hide 419b7");
  assert.match(h.sent.at(-1).text, /edited messages are not run again/);
  assert.deepEqual(h.stored("state/prefs.json").hidden, []);
  const dispatchedBefore = h.dispatched.length;
  await edited("/run");
  assert.equal(h.dispatched.length, dispatchedBefore);
  await edited("/jobs");
  assert.match(h.sent.at(-1).text, /Top 1 of 1 current matches/);  // a view may be opened again
});

test("preferences are never written blind: unreadable file, and a write that lost a race", async () => {
  const h = harness({ index: makeIndex([job("gh:acme:1")]), prefs: { applied: { x: { title: "Kept" } } } });
  h.objects.set("state/prefs.json", "{ not json");
  await h.say("/pause");
  assert.match(h.sent.at(-1).text, /could not be read, so nothing was changed/);
  assert.equal(h.objects.get("state/prefs.json"), "{ not json");
  // conditional put: the first attempt loses against a write from the Python side, the second is made on the fresh copy
  h.objects.set("state/prefs.json", JSON.stringify({ muted: [], hidden: [], applied: {} }));
  let version = 1;
  let refused = 0;
  h.env.INBOX.get = async (key) => (h.objects.has(key) ? { etag: `v${version}`, json: async () => JSON.parse(h.objects.get(key)) } : null);
  h.env.INBOX.put = async (key, value, options) => {
    if (key === "state/prefs.json" && refused === 0) {  // Python wrote /watch in between
      refused += 1; version += 1;
      h.objects.set(key, JSON.stringify({ muted: [], hidden: [], applied: {}, watch: [{ name: "Fugro" }] }));
      assert.deepEqual(options.onlyIf, { etagMatches: "v1" });
      return null;
    }
    h.objects.set(key, value); return {};
  };
  await h.say("/mute leidos");
  const prefs = h.stored("state/prefs.json");
  assert.deepEqual(prefs.muted, ["leidos"]);
  assert.deepEqual(prefs.watch, [{ name: "Fugro" }]);  // the other side's write survived
});

test("long replies are cut between blocks, never through a tag", async () => {
  const many = Array.from({ length: 60 }, (_, i) => job(`gh:bulk:${i}`, { code: `c${String(i).padStart(4, "0")}`, t: `GIS Analyst ${i}`, c: `Firm ${i}`,
    ai: { fit: 7, summary: "A long summary of the role and why it fits the candidate rather well. ".repeat(3) } }));
  const h = harness({ index: makeIndex(many) });
  await h.say("/ai 25");
  assert.ok(h.sent.length >= 2);
  for (const message of h.sent) {
    assert.ok(message.text.length <= 4096);
    assert.equal((message.text.match(/<a /g) || []).length, (message.text.match(/<\/a>/g) || []).length);
    assert.equal(message.parse_mode, "HTML");
  }
});

test("mail: newsletters, acknowledgements that mention interviews, and outcomes already recorded", async () => {
  const applied = { "gh:acme:1": { title: "GIS Analyst", company: "Acme Geospatial Ltd", at: iso(100), status: "offer" },
    "lever:old:9": { title: "Cartographer", company: "MapCo", at: iso(200), status: "applied" } };
  const h = harness({ index: makeIndex([]), prefs: { applied } });
  const run = async (m) => { const pending = []; await worker.email(m, h.env, { waitUntil: (p) => pending.push(p) }); await Promise.all(pending); };
  // an offer on record is not overwritten by a later mail that reads like a rejection
  await run(mail({ from: "talent@acmegeospatial.com", subject: "Update", body: "Unfortunately the other position has been filled." }));
  assert.equal(h.stored("state/prefs.json").applied["gh:acme:1"].status, "offer");
  assert.match(h.sent.at(-1).text, /Not recorded \(it is already 'offer'\)/);
  // "if selected for an interview" in an acknowledgement is not an invitation
  await run(mail({ from: "hr@mapco.example", subject: "Thank you for applying", body: "We have received your application. If you are selected for an interview we will contact you." }));
  assert.equal(h.stored("state/prefs.json").applied["lever:old:9"].status, "applied");
  assert.match(h.sent.at(-1).text, /an acknowledgement/);
  // a newsletter that only names the employer is a weak link: reported, not recorded
  const news = mail({ from: "digest@jobboard.example", subject: "This week", body: "MapCo and others: unfortunately many roles closed. Other candidates were faster." });
  await run(news);
  assert.equal(h.stored("state/prefs.json").applied["lever:old:9"].status, "applied");
  assert.match(h.sent.at(-1).text, /Not recorded \(the link to that application is weak\)/);
  // nested multipart (mixed > alternative) is read
  const b = "outer", inner = "inner";
  const raw = ["From: hr@mapco.example", "Subject: Cartographer", `Content-Type: multipart/mixed; boundary="${b}"`, "", `--${b}`,
    `Content-Type: multipart/alternative; boundary="${inner}"`, "", `--${inner}`, "Content-Type: text/plain; charset=utf-8", "",
    "We would like to invite you to an interview. What is your availability for a call?", `--${inner}--`, `--${b}`,
    "Content-Type: application/pdf", "Content-Transfer-Encoding: base64", "", "JVBERi0=", `--${b}--`, ""].join("\r\n");
  await run({ from: "hr@mapco.example", headers: new Map([["subject", "Cartographer"]]), raw: new Response(raw).body });
  assert.equal(h.stored("state/prefs.json").applied["lever:old:9"].status, "interview");
});

// ---------------------------------------------------------------------------- digest buttons (REVIEW O1)
// The grid a digest is sent with (geojobbot.notifications.telegram.digest_keyboard): one number per entry.
const GRID = [[{ text: "1", callback_data: "m|419b7" }, { text: "2", callback_data: "m|2fd2a33338a518c" }]];
const actionRowOf = (keyboard) => keyboard.find((row) => !row.every((b) => b.callback_data.startsWith("m|")));
const buttonsIndex = () => makeIndex([job("gh:acme:1"), job("gh:acme:2", { t: "Surveyor" }), job("gh:acme:3", { t: "Surveyor" }),
  job("gh:acme:4", { t: "Surveyor" })]);
async function capturingLogs(body) {
  const logged = [];
  const log = console.log;
  console.log = (...args) => logged.push(args.join(" "));
  try { await body(logged); } finally { console.log = log; }
}

test("taps from anyone but the owner get an empty answer and change nothing", async () => {
  const h = harness({ index: buttonsIndex() });
  assert.equal(await (await h.tap("a|419b7", { keyboard: GRID, from: 7 })).text(), "ok");
  await h.tap("a|419b7", { keyboard: GRID, from: 7, chat: 7 });
  await h.tap("a|419b7", { message: false });  // inline mode: no message whose chat could be checked
  await h.tap("a|419b7", { keyboard: GRID, chat: 99 });  // the owner, but in a chat that is not the bot's
  assert.deepEqual(h.of("answerCallbackQuery").map((a) => a.text), ["", "", "", ""]);
  assert.deepEqual(h.writes, []);
  assert.deepEqual(h.reads, []);
  // a group without TELEGRAM_OWNER_ID: the owner is the group itself, so every tap is refused (as documented)
  const group = harness({ index: buttonsIndex() });
  group.env.TELEGRAM_CHAT_ID = "-100123";
  await group.tap("a|419b7", { keyboard: GRID, chat: -100123, from: 42 });
  assert.deepEqual(group.writes, []);
  group.env.TELEGRAM_OWNER_ID = "42";
  await group.tap("a|419b7", { keyboard: GRID, chat: -100123, from: 42 });
  assert.ok(group.stored("state/prefs.json").applied["gh:acme:1"]);
});

test("a number opens its action row and the cross closes it, without reading the bucket", async () => {
  const h = harness({ index: buttonsIndex() });
  await h.tap("m|2fd2a33338a518c", { keyboard: GRID });
  assert.deepEqual(h.reads, []);
  const [edit] = h.of("editMessageReplyMarkup");
  assert.equal(edit.message_id, 7);
  assert.deepEqual(edit.reply_markup.inline_keyboard, [GRID[0], [
    { text: "✅ Applied 2", callback_data: "a|2fd2a" }, { text: "🙈 Hide 2", callback_data: "h|2fd2a33338a518c" },
    { text: "❓ Why 2", callback_data: "w|2fd2a" }, { text: "✖", callback_data: "x|" }]]);
  await h.tap("x|", { keyboard: edit.reply_markup.inline_keyboard });
  assert.deepEqual(h.of("editMessageReplyMarkup")[1].reply_markup.inline_keyboard, GRID);
  assert.deepEqual(h.of("answerCallbackQuery").map((a) => a.text), ["", ""]);
  assert.deepEqual(h.reads, []);
  for (const data of ["z|419b7", "a|419B7", "a|419b7419b7419b7419b7", "a|"]) await h.tap(data, { keyboard: GRID });
  assert.deepEqual(h.of("answerCallbackQuery").slice(2).map((a) => a.text), Array(4).fill("Unknown button"));
  assert.deepEqual(h.reads, []);
});

test("Applied writes the application, relabels the number, and a second tap writes nothing", async () => {
  const h = harness({ index: buttonsIndex() });
  await h.tap("a|419b7", { keyboard: [...GRID, [{ text: "✅ Applied 1", callback_data: "a|419b7" }]] });
  const application = h.stored("state/prefs.json").applied["gh:acme:1"];
  assert.equal(application.status, "applied");
  assert.deepEqual(application.history.map((e) => e.status), ["applied"]);
  const answers = h.of("answerCallbackQuery");
  assert.equal(answers.length, 1);
  assert.match(answers[0].text, /^✅ Marked as applied: GIS Analyst\. Good luck/);  // a toast: plain text, at most 200
  assert.ok(answers[0].text.length <= 200 && !answers[0].show_alert);
  const keyboard = h.of("editMessageReplyMarkup")[0].reply_markup.inline_keyboard;
  assert.equal(keyboard[0][0].text, "1✅");
  assert.equal(keyboard.length, 2);  // the old action row was replaced, not kept
  assert.deepEqual(actionRowOf(keyboard).map((b) => b.callback_data), ["r|419b7", "h|419b7", "w|419b7", "x|"]);
  const writes = h.writes.length;
  await h.tap("a|419b7", { keyboard });  // a second tap: mutate sees the application and writes nothing
  assert.match(h.of("answerCallbackQuery")[1].text, /^Already recorded: GIS Analyst · applied/);
  assert.equal(h.writes.length, writes);
  await h.tap("a|419b7", { keyboard: GRID });  // a stale label: mutate sees the application and writes nothing either
  assert.match(h.of("answerCallbackQuery")[2].text, /^Already recorded: GIS Analyst · applied/);
  assert.equal(h.writes.length, writes);
});

test("Undo applied removes only an application that has no outcome yet", async () => {
  const h = harness({ index: buttonsIndex() });
  const applied = [[{ text: "1✅", callback_data: "m|419b7" }]];
  await h.tap("a|419b7", { keyboard: GRID });
  await h.tap("r|419b7", { keyboard: applied });
  assert.deepEqual(h.stored("state/prefs.json").applied, {});
  assert.match(h.of("answerCallbackQuery")[1].text, /No longer marked as applied: GIS Analyst/);
  const keyboard = h.of("editMessageReplyMarkup")[1].reply_markup.inline_keyboard;
  assert.equal(keyboard[0][0].text, "1");
  assert.equal(actionRowOf(keyboard)[0].callback_data, "a|419b7");
  await h.say("/applied 419b7");
  await h.say("/outcome 419b7 interview");
  await h.tap("r|419b7", { keyboard: applied });
  assert.match(h.of("answerCallbackQuery").at(-1).text, /Already has an outcome \(interview\)/);
  assert.equal(h.stored("state/prefs.json").applied["gh:acme:1"].status, "interview");
});

test("Hide acts on every posting of a grouped entry, once, and Unhide brings them back", async () => {
  const h = harness({ index: buttonsIndex() });
  await h.tap("h|2fd2a33338a518c", { keyboard: GRID });
  const prefs = h.stored("state/prefs.json");
  assert.deepEqual(prefs.hidden, ["gh:acme:2", "gh:acme:3", "gh:acme:4"]);
  assert.equal(prefs.hidden_info["gh:acme:4"].title, "Surveyor");
  assert.deepEqual(h.writes, ["state/prefs.json"]);
  assert.equal(h.of("answerCallbackQuery")[0].text, "🙈 Hidden: Surveyor ×3");
  const keyboard = h.of("editMessageReplyMarkup")[0].reply_markup.inline_keyboard;
  assert.equal(keyboard[0][1].text, "2🙈");
  assert.deepEqual(actionRowOf(keyboard).map((b) => b.callback_data), ["u|2fd2a33338a518c", "w|2fd2a", "x|"]);
  await h.tap("h|2fd2a33338a518c", { keyboard });
  assert.equal(h.of("answerCallbackQuery")[1].text, "🙈 Hidden: Surveyor ×3");
  assert.equal(h.writes.length, 1);  // already hidden: nothing written
  await h.tap("u|2fd2a33338a518c", { keyboard });
  assert.deepEqual(h.stored("state/prefs.json").hidden, []);
  assert.equal(h.of("editMessageReplyMarkup").at(-1).reply_markup.inline_keyboard[0][1].text, "2");
  assert.equal(h.writes.length, 2);
  // a typed /hide of a job that is already hidden writes nothing either
  await h.say("/hide 419b7");
  await h.say("/hide 419b7");
  assert.equal(h.writes.length, 3);
});

test("'message is not modified' counts as success; an inaccessible message gets a toast only", async () => {
  await capturingLogs(async (logged) => {
    const h = harness({ index: buttonsIndex(), telegramAnswer: (method) => (method === "editMessageReplyMarkup"
      ? { status: 400, body: { ok: false, error_code: 400, description: "Bad Request: message is not modified: specified new message content and reply markup are exactly the same" } } : null) });
    await h.tap("m|419b7", { keyboard: GRID });
    assert.equal(h.of("answerCallbackQuery").length, 1);
    assert.ok(!logged.some((line) => line.startsWith("keyboard not updated")));
    assert.ok(logged.includes('callback {"action":"m","ok":true}'));
    assert.ok(!logged.some((line) => line.includes("419b7")));  // codes never reach the logs
  });
  const old = harness({ index: buttonsIndex() });
  await old.tap("a|419b7", { date: 0 });
  assert.ok(old.stored("state/prefs.json").applied["gh:acme:1"]);
  assert.match(old.of("answerCallbackQuery")[0].text, /Marked as applied/);
  assert.equal(old.of("editMessageReplyMarkup").length, 0);
  await old.tap("m|419b7", { date: 0 });
  assert.match(old.of("answerCallbackQuery")[1].text, /too old for its buttons\. \/why 419b7/);
  assert.equal(old.of("editMessageReplyMarkup").length, 0);
});

test("a tap that loses a race is retried once on the fresh preferences", async () => {
  const h = harness({ index: buttonsIndex(), prefs: { muted: [], hidden: [], applied: {} } });
  let version = 1;
  let refused = 0;
  h.env.INBOX.get = async (key) => (h.objects.has(key) ? { etag: `v${version}`, json: async () => JSON.parse(h.objects.get(key)) } : null);
  h.env.INBOX.put = async (key, value, options) => {
    if (key === "state/prefs.json" && refused === 0) {  // Python wrote /watch in between
      refused += 1; version += 1;
      h.objects.set(key, JSON.stringify({ muted: [], hidden: [], applied: {}, watch: [{ name: "Fugro" }] }));
      assert.deepEqual(options.onlyIf, { etagMatches: "v1" });
      return null;
    }
    h.objects.set(key, value); return {};
  };
  await h.tap("h|419b7", { keyboard: GRID });
  const prefs = h.stored("state/prefs.json");
  assert.deepEqual(prefs.hidden, ["gh:acme:1"]);
  assert.deepEqual(prefs.watch, [{ name: "Fugro" }]);
  assert.equal(refused, 1);
  assert.equal(h.of("answerCallbackQuery").length, 1);
  assert.equal(h.of("editMessageReplyMarkup")[0].reply_markup.inline_keyboard[0][0].text, "1🙈");
  // an unreadable file throws: still exactly one answer, as an alert, and nothing written
  h.objects.set("state/prefs.json", "{ not json");
  await h.tap("a|419b7", { keyboard: GRID });
  const answers = h.of("answerCallbackQuery");
  assert.equal(answers.length, 2);
  assert.match(answers[1].text, /could not be read, so nothing was changed/);
  assert.equal(answers[1].show_alert, true);
  assert.equal(h.objects.get("state/prefs.json"), "{ not json");
});

test("a code that left the index goes to Python as the tapped command, under the update id", async () => {
  const h = harness({ index: makeIndex([]) });
  const grid = [[{ text: "1", callback_data: "m|806ad" }]];
  await h.tap("h|806ad", { keyboard: grid });
  assert.equal(h.stored("inbox/000000000100.json").text, "/hide 806ad");
  assert.equal(h.of("answerCallbackQuery")[0].show_alert, false);  // "On it" follows as a message: a toast is enough
  assert.match(h.of("answerCallbackQuery")[0].text, /^Passed on to the full history/);
  await h.tap("u|806ad", { keyboard: [[{ text: "1🙈", callback_data: "m|806ad" }]] });
  assert.equal(h.stored("inbox/000000000101.json").text, "/unhide 806ad");
  await h.tap("a|806ad", { keyboard: grid });
  assert.equal(h.stored("inbox/000000000102.json").text, "/applied 806ad");
  await h.tap("w|806ad", { keyboard: grid });
  assert.equal(h.stored("inbox/000000000103.json").text, "/why 806ad");
  assert.equal(h.dispatched.length, 4);
  assert.equal(h.of("answerCallbackQuery").length, 4);
  // Why on a job in the index is answered at once, as a new message
  const known = harness({ index: buttonsIndex() });
  await known.tap("w|419b7", { keyboard: GRID });
  assert.equal(known.of("answerCallbackQuery")[0].text, "");
  assert.match(known.sent[0].text, /Score 82\/100 · high/);
  assert.equal(known.dispatched.length, 0);
});

test("replies: silent answers, loud news, one retry after flood control, plain text only for bad markup", async () => {
  const h = harness({ index: makeIndex([job("gh:acme:1")]) });
  await h.say("/status");
  assert.equal(h.sent[0].disable_notification, true);
  assert.deepEqual(h.sent[0].link_preview_options, { is_disabled: true });
  assert.equal(h.sent[0].disable_web_page_preview, undefined);
  await h.say("/pitch 419b7");
  assert.equal(h.sent[1].disable_notification, true);  // "On it"
  const stale = makeIndex([]);
  stale.generated_at = iso(9);
  const dog = harness({ index: stale });
  await dog.tick();
  assert.equal(dog.sent[0].disable_notification, undefined);  // the watchdog rings
  const mailbox = harness({ index: makeIndex([]), prefs: { applied: { "lever:old:9": { title: "Cartographer", company: "MapCo", at: iso(200), status: "applied" } } } });
  const pending = [];
  await worker.email(mail({ from: "hr@mapco.example", subject: "Interview: Cartographer role",
    body: "We would like to invite you to an interview next week. What is your availability for a call?" }), mailbox.env, { waitUntil: (p) => pending.push(p) });
  await Promise.all(pending);
  assert.equal(mailbox.sent[0].disable_notification, undefined);  // an interview invitation rings
  // 429: the same HTML once more after retry_after
  let attempts = 0;
  const flooded = harness({ index: makeIndex([job("gh:acme:1")]), telegramAnswer: (method) => (method === "sendMessage" && attempts++ === 0
    ? { status: 429, body: { ok: false, error_code: 429, description: "Too Many Requests: retry after 1", parameters: { retry_after: 1 } } } : null) });
  await flooded.say("/status");
  assert.equal(flooded.sent.length, 2);
  assert.equal(flooded.sent[1].text, flooded.sent[0].text);
  assert.equal(flooded.sent[1].parse_mode, "HTML");
  // "can't parse entities": the text again, decoded, without markup and as quiet as the original
  const broken = harness({ index: makeIndex([job("gh:acme:1", { t: "GIS & Survey <Lead>" })]), telegramAnswer: (method, body) => (body.parse_mode
    ? { status: 400, body: { ok: false, description: "Bad Request: can't parse entities: unsupported start tag" } } : null) });
  await broken.say("/why 419b7");
  assert.equal(broken.sent.length, 2);
  assert.equal(broken.sent[1].parse_mode, undefined);
  assert.match(broken.sent[1].text, /^GIS & Survey <Lead> — Acme/);
  assert.equal(broken.sent[1].disable_notification, true);
  // anything else is logged, not resent
  await capturingLogs(async (logged) => {
    const refused = harness({ index: makeIndex([job("gh:acme:1")]), telegramAnswer: () => ({ status: 403, body: { ok: false, description: "Forbidden: bot was blocked by the user" } }) });
    await refused.say("/status");
    assert.equal(refused.sent.length, 1);
    assert.ok(logged.some((line) => line.startsWith("reply not delivered") && line.includes("403")));
  });
});

test("without the INBOX binding a tap hands the typed command to Python; Undo is never handed over", async () => {
  await capturingLogs(async (logged) => {
    const h = harness({ index: buttonsIndex(), inbox: false });
    await h.tap("a|419b7", { keyboard: GRID });
    await h.tap("h|2fd2a33338a518c", { keyboard: GRID });
    await h.tap("u|419b7", { keyboard: GRID });
    assert.deepEqual(h.dispatched.map((d) => d.inputs.text), ["/applied 419b7", "/hide 2fd2a", "/unhide 419b7"]);
    await h.tap("r|419b7", { keyboard: [[{ text: "1✅", callback_data: "m|419b7" }]] });
    assert.equal(h.dispatched.length, 3);
    const answers = h.of("answerCallbackQuery");
    assert.equal(answers.length, 4);
    assert.match(answers[3].text, /^Undo needs the Worker's INBOX binding/);
    assert.equal(answers[3].show_alert, true);
    assert.ok(!logged.some((line) => line.startsWith("callback error")));
  });
});

test("Undo works from the preferences alone when the index is missing, and never asks Python for /unapply", async () => {
  const applied = { "gh:acme:1": { title: "GIS Analyst", status: "applied", at: iso(5), history: [{ at: iso(5), status: "applied" }] } };
  const h = harness({ prefs: { applied } });
  await h.tap("r|419b7", { keyboard: [[{ text: "1✅", callback_data: "m|419b7" }]] });
  assert.deepEqual(h.stored("state/prefs.json").applied, {});
  assert.match(h.of("answerCallbackQuery")[0].text, /No longer marked as applied: GIS Analyst/);
  assert.equal(h.of("editMessageReplyMarkup")[0].reply_markup.inline_keyboard[0][0].text, "1");
  await h.tap("r|419b7", { keyboard: [[{ text: "1✅", callback_data: "m|419b7" }]] });
  assert.equal(h.of("answerCallbackQuery")[1].text, "That job isn't recorded as applied.");
  assert.deepEqual(h.dispatched, []);
  assert.ok(![...h.objects.keys()].some((key) => key.startsWith("inbox/")));
});

test("a stale label is not trusted: Hide after a typed /unhide hides again", async () => {
  const h = harness({ index: buttonsIndex() });
  await h.tap("h|419b7", { keyboard: GRID });
  const keyboard = h.of("editMessageReplyMarkup")[0].reply_markup.inline_keyboard;
  assert.equal(keyboard[0][0].text, "1🙈");
  await h.say("/unhide 419b7");
  assert.deepEqual(h.stored("state/prefs.json").hidden, []);
  await h.tap("h|419b7", { keyboard });  // the label still says hidden
  assert.deepEqual(h.stored("state/prefs.json").hidden, ["gh:acme:1"]);
  assert.match(h.of("answerCallbackQuery")[1].text, /^🙈 Hidden: GIS Analyst/);
  // and Unhide from a label that says visible restores it, relabelling from what is stored
  await h.tap("u|419b7", { keyboard: GRID });
  assert.deepEqual(h.stored("state/prefs.json").hidden, []);
  assert.equal(h.of("editMessageReplyMarkup").at(-1).reply_markup.inline_keyboard[0][0].text, "1");
});

test("a toast is cut at 200 characters between characters, never through an emoji", async () => {
  const prefix = "✅ Marked as applied: ";
  const title = `${"x".repeat(199 - prefix.length)}🌍 Survey`;  // the globe's first half would be character 200
  const h = harness({ index: makeIndex([job("gh:acme:1", { t: title })]) });
  await h.tap("a|419b7", { keyboard: GRID });
  const [answer] = h.of("answerCallbackQuery");
  assert.ok(answer.text.isWellFormed());
  assert.equal(answer.text.length, 199);
  assert.ok(answer.text.endsWith("x"));
});
