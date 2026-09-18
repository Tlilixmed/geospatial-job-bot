// Tests for cloudflare/worker.js with a fake R2 bucket, fake Telegram/GitHub and a fake Workers AI.
// Run: node --test tests/worker/worker.test.mjs
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const source = await readFile(new URL("../../cloudflare/worker.js", import.meta.url), "utf8");
const worker = (await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`)).default;

const HOUR = 36e5;
const iso = (hoursAgo) => new Date(Date.now() - hoursAgo * HOUR).toISOString();
const CODES = { "gh:acme:1": "419b7", "gh:acme:2": "2fd2a", "lever:old:9": "806ad" };  // geojobbot.utils.text.job_code

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

function harness({ index, prefs, ai, inbox = true, prefix = "" } = {}) {
  const objects = new Map();
  if (index) objects.set(`${prefix}state/index.json`, JSON.stringify(index));
  if (prefs) objects.set(`${prefix}state/prefs.json`, JSON.stringify(prefs));
  const sent = [];
  const dispatched = [];
  const env = { TELEGRAM_BOT_TOKEN: "T", TELEGRAM_CHAT_ID: "42", WEBHOOK_SECRET: "s3cret", GITHUB_TOKEN: "gh", GITHUB_REPO: "o/r" };
  if (inbox) {
    env.INBOX = {
      get: async (key) => (objects.has(key) ? { json: async () => JSON.parse(objects.get(key)) } : null),
      put: async (key, value) => { objects.set(key, value); },
    };
  }
  if (prefix) env.STATE_PREFIX = prefix;
  if (ai) env.AI = { run: async () => ({ response: ai }) };
  globalThis.fetch = async (url, init) => {
    const body = JSON.parse(init.body);
    if (String(url).includes("api.telegram.org")) sent.push(body); else dispatched.push(body);
    return { ok: true, status: 200 };
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
  const stored = (key) => (objects.has(key) ? JSON.parse(objects.get(key)) : null);
  return { say, sent, dispatched, stored, objects };
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
  assert.equal(h.dispatched.length, 0);
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

test("strangers and wrong secrets are refused", async () => {
  const h = harness({ index: makeIndex([job("gh:acme:1")]) });
  assert.equal((await h.say("/jobs", { secret: "nope" })).status, 403);
  assert.equal(await (await h.say("/jobs", { chat: 7, from: 7 })).text(), "ignored");
  assert.equal(h.sent.length, 0);
  await h.say("/jobs", { chat: -100123, from: 42 });  // the owner, writing from a group
  assert.equal(h.sent.length, 1);
  assert.equal(h.sent[0].chat_id, "42");
});
