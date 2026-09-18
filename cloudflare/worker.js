/**
 * Telegram front door of the geospatial job bot.
 *
 * Telegram calls this Worker the instant you send a message. With the INBOX (R2) binding the Worker answers most
 * things itself, in under a second, from two files the Python side maintains in the bucket:
 *   state/index.json   current matches, codes, scores, AI notes, sponsor hits, run status, help text (read only)
 *   state/prefs.json   your preferences and applications (read and written here and by Python; same schema)
 * Everything else (/run, /pitch, /weekly, /radar, /skills, /learning, and free-text sentences that would change a
 * setting) starts .github/workflows/commands.yml, where the Python rules decide; that reply takes about a minute.
 *
 * Secrets (Settings -> Variables and secrets, type "Secret"):
 *   TELEGRAM_BOT_TOKEN   the bot token from BotFather
 *   TELEGRAM_CHAT_ID     the chat alerts and replies go to (same value as the GitHub secret); /id shows it
 *   WEBHOOK_SECRET       any random string of letters and digits; also given to Telegram in setWebhook
 *   GITHUB_TOKEN         fine-grained personal access token: this repository only, Actions = Read and write
 *   GITHUB_REPO          e.g. Tlilixmed/geospatial-job-bot
 *   TELEGRAM_OWNER_ID    optional: your own Telegram user id, when TELEGRAM_CHAT_ID is a group or channel
 *   STATE_PREFIX         optional: only when the GitHub side sets STATE_PREFIX (same value)
 *
 * Who is obeyed: messages in TELEGRAM_CHAT_ID, and messages written by the owner in any chat the bot can read.
 *
 * Bindings (Settings -> Bindings -> Add):
 *   INBOX  R2 bucket = the bot's bucket. Enables instant replies and the private hand-over of messages
 *          (inbox/<id>.json instead of workflow inputs, which are world-readable on a public repository).
 *   AI     Workers AI (optional). Reads unusual free-text sentences. Its answer must be a known command, and on
 *          its own it may only open read-only views; anything that changes settings goes to the Python rules.
 */
const COMMANDS = ["jobs", "high", "range", "search", "why", "applied", "hide", "unhide", "mute", "unmute", "muted",
  "threshold", "locations", "interns", "pause", "resume", "status", "weekly", "run", "help", "pitch", "ai", "sponsors",
  "outcome", "possible", "radar", "skills", "signals", "learning"];
const HINT_RE = new RegExp(`^/(${COMMANDS.join("|")})(\\s[^\\n]{0,100})?$`);
const FAST_READ = new Set(["jobs", "top", "high", "range", "search", "why", "ai", "sponsors", "sponsor", "status", "help",
  "start", "muted", "signals"]);
const FAST_WRITE = new Set(["applied", "outcome", "hide", "unhide", "mute", "unmute", "threshold", "locations", "interns",
  "possible", "pause", "resume"]);
const STATUSES = { applied: "📨", interview: "🎤", offer: "🎉", rejected: "❌", withdrawn: "↩️", ghosted: "👻" };
const INDEX_KEY = "state/index.json";
const PREFS_KEY = "state/prefs.json";
const AI_TIMEOUT_MS = 6000;
const MAX_MESSAGE = 4000;

const SYSTEM_PROMPT = `You translate one chat message (English, French or Arabic, typos possible) sent to a job-alert bot
into exactly ONE bot command. Answer with the command line only, no explanation, no quotes.
Commands:
/jobs [n]            best current job matches (n = how many)
/high [n]            only the strongest matches
/range LOW HIGH      jobs whose score (0-100) is between LOW and HIGH ("above 80" -> /range 80 100)
/search WORDS        look for jobs about a skill, title, company or place (keep only the meaningful words)
/why CODE            explain one job; CODE is a 5-character tag like a3f9c
/pitch CODE          write a cover letter / application note for that job
/ai [n]              show the AI's opinion (fit, summary, concerns) of the current matches
/sponsors [n]        jobs from employers on official visa-sponsor registers, or that offer sponsorship
/applied CODE        the user applied to that job        /applied   list applications
/outcome CODE STATUS what happened to an application: interview, offer, rejected, withdrawn or ghosted
/hide CODE           the user is not interested          /unhide CODE   bring it back
/mute TEXT           stop showing a company or title word /unmute TEXT   /muted  list mutes
/threshold HIGH [POSSIBLE]   change score cut-offs
/locations A, B      preferred countries                 /locations reset
/interns on|off      include or exclude internships
/possible on|off     also alert on weaker "possible" matches, or only on strong ones
/pause  /resume      stop or restart alerts
/status              is the bot healthy, last run, settings
/weekly              summary of applications and open matches
/radar               which skills the market asks for and which the user lacks
/signals             firms that won geospatial contracts, consultancies, tenders
/learning            what the bot learned from the user's applications and hidden jobs
/run                 search for new jobs right now
/help                what the bot can do
If the message is a question about jobs of some kind, use /search. If nothing fits, answer /help.`;

// ---------------------------------------------------------------------------- small helpers
const esc = (value) => String(value == null ? "" : value)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const fold = (value) => String(value || "").normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase()
  .replace(/\s+/g, " ").trim();
const clampInt = (value, fallback, low, high) => {
  const n = parseInt(String(value || "").trim().split(/\s+/)[0], 10);
  return Number.isFinite(n) ? Math.max(low, Math.min(high, n)) : fallback;
};

/** Same 5-character handle Python derives from a job id (geojobbot.utils.text.job_code). */
async function codeOf(id) {
  const digest = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(String(id)));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 5);
}

function telegram(env, chatId, text, html) {
  const body = { chat_id: chatId, text, disable_web_page_preview: true };
  if (html) body.parse_mode = "HTML"; else body.disable_notification = true;
  return fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
}
const tell = (env, text) => telegram(env, env.TELEGRAM_CHAT_ID, text, false);

async function reply(env, messages) {
  for (const message of messages) {
    const response = await telegram(env, env.TELEGRAM_CHAT_ID, message.slice(0, 4096), true);
    if (!response.ok) await tell(env, message.replace(/<[^>]+>/g, "").slice(0, 4000));  // bad markup: send it plain
  }
}

/** Keys follow the bot's STATE_PREFIX (a plain Worker variable, only needed when the GitHub side sets one). */
const keyOf = (env, suffix) => {
  const prefix = String(env.STATE_PREFIX || "").replace(/^[/]+|[/]+$/g, "");
  return prefix ? `${prefix}/${suffix}` : suffix;
};

async function readJson(env, key) {
  const object = await env.INBOX.get(keyOf(env, key));
  if (!object) return null;
  try { return await object.json(); } catch { return null; }
}

function defaultPrefs() {
  return { paused: false, muted: [], hidden: [], applied: {}, high_threshold: null, medium_threshold: null,
    preferred_locations: null, exclude_internships: null, notify_possible: null, hidden_info: {}, learning: null,
    learning_since: null, my_skills: null };
}
async function loadPrefs(env) {
  const stored = await readJson(env, PREFS_KEY);
  const prefs = defaultPrefs();
  if (stored && typeof stored === "object") for (const key of Object.keys(prefs)) if (key in stored) prefs[key] = stored[key];
  return prefs;
}
const savePrefs = (env, prefs) => env.INBOX.put(keyOf(env, PREFS_KEY),
  JSON.stringify({ ...prefs, updated_at: new Date().toISOString() }, null, 2), { httpMetadata: { contentType: "application/json" } });

async function aiHint(env, text) {
  const result = await env.AI.run("@cf/meta/llama-3.1-8b-instruct", {
    messages: [{ role: "system", content: SYSTEM_PROMPT }, { role: "user", content: text.slice(0, 300) }],
    max_tokens: 40,
    temperature: 0,
  });
  const line = String((result && result.response) || "").trim().split("\n")[0].trim().replace(/^["'`]|["'`.]$/g, "");
  return HINT_RE.test(line) ? line : "";
}

// ---------------------------------------------------------------------------- views over the index
function isCurrent(job, index, prefs, now) {
  const settings = index.settings || {};
  if (job.rel && job.p) {
    const limit = job.rot ? settings.rotation_max_age_h : settings.max_age_h;
    if (limit && (now - Date.parse(job.p)) / 36e5 > limit) return false;
  }
  if (job.seen && now - Date.parse(job.seen) > (job.rot ? 21 : 5) * 864e5) return false;
  if (prefs.hidden.includes(job.id) || job.id in prefs.applied) return false;
  const haystack = fold(`${job.t || ""} ${job.c || ""}`);
  return !prefs.muted.some((term) => term.trim() && haystack.includes(fold(term)));
}

function currentMatches(index, prefs, onlyHigh) {
  const now = Date.now();
  return index.jobs.filter((j) => (onlyHigh ? j.tier === "high" : j.tier === "high" || j.tier === "possible")
    && isCurrent(j, index, prefs, now));
}

function groupSame(jobs) {
  const groups = new Map();
  for (const job of jobs) {
    const key = job.c ? `${fold(job.c)}|${fold(job.t)}` : `#${job.id}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(job);
  }
  return [...groups.values()];
}

function sponsorBadge(job) {
  const hits = job.sp || [];
  if (!hits.length) return "";
  const hit = hits.find((h) => h.country === job.country) || hits[0];
  let text = `${hit.icon || "🛂"} ${hit.label}`;
  if (hit.geo) text += ` (hired ${(hit.occupations || ["geomatics staff"]).join(", ").slice(0, 40)})`;
  if (job.country && hit.country !== job.country) text += " — not this country";
  return text;
}

function entry(same, number) {
  const job = same[0];
  let head = `${number}. <b>${esc(job.t)}</b>`;
  if (job.c) head += ` — ${esc(job.c)}`;
  if (same.length > 1) head += ` ×${same.length}`;
  head += ` · ${job.s}/100 · ` + same.slice(0, 3).map((j) => `<code>${j.code}</code>`).join(" ");
  const places = [...new Set(same.map((j) => j.loc))];
  const facts = [`📍 ${esc(places.slice(0, 3).join(" | "))}`];
  if (job.p) facts.push(`📅 ${new Date(job.p).toUTCString().slice(5, 11)}`);
  if (job.sal) facts.push(`💰 ${esc(job.sal)}`);
  if (job.offered) facts.push("🛂 sponsorship offered");
  const badge = sponsorBadge(job);
  if (badge) facts.push(esc(badge));
  const lines = [head, `   ${facts.join(" · ")}`];
  if (job.ai && job.ai.summary) lines.push(`   💡 ${esc(job.ai.summary)}`);
  if (job.ai && job.ai.concerns) lines.push(`   ⚠️ ${esc(job.ai.concerns)}`);
  const tail = [];
  const skills = [...new Set((job.sk || []).map((s) => s.split(" (")[0]))].slice(0, 4);
  if (skills.length) tail.push(esc(skills.join(", ")));
  if (job.url) tail.push(`<a href="${esc(job.url)}">Apply</a>`);
  if (tail.length) lines.push(`   ${tail.join(" · ")}`);
  return lines.join("\n");
}

function listing(jobs, title, empty) {
  if (!jobs.length) return [empty];
  const high = jobs.filter((j) => j.tier === "high");
  const rest = jobs.filter((j) => j.tier !== "high");
  const header = `🗺️ <b>${esc(title)}</b>\n<i>${high.length} high · ${rest.length} other</i>`;
  const messages = [];
  let current = header;
  let number = 0;
  for (const [label, tierJobs] of [["🔥 <b>High matches</b>", high], ["🟡 <b>Possible and near misses</b>", rest]]) {
    let first = true;
    for (const same of groupSame(tierJobs)) {
      number += 1;
      const block = `\n\n${first ? `${label}\n\n` : ""}${entry(same, number)}`;
      first = false;
      if (current.length + block.length > MAX_MESSAGE) { messages.push(current); current = `🗺️ <b>${esc(title)}</b> (cont.)`; }
      current += block;
    }
  }
  messages.push(current);
  return messages;
}

const findByCode = (index, code) => {
  const wanted = String(code || "").trim().toLowerCase().replace(/[<>\[\]#]/g, "");
  return wanted ? index.jobs.find((j) => j.code === wanted || String(j.id).toLowerCase() === wanted) : undefined;
};
const snapshot = (job) => ({ title: job.t, company: job.c, country: job.country,
  skills: (job.sk || []).map((s) => s.split(" (")[0]).slice(0, 12), domains: (job.dom || []).slice(0, 12) });

// ---------------------------------------------------------------------------- commands answered here
function viewWhy(index, arg) {
  const job = findByCode(index, arg);
  if (!job) return ["Usage: /why code — the 5-character tag next to a job."];
  const bd = job.bd || {};
  const lines = [`<b>${esc(job.t)}</b>${job.c ? ` — ${esc(job.c)}` : ""}`, `Score ${job.s}/100 · ${esc(job.tier)}`,
    `Title ${bd.title || 0} · skills ${bd.tech || 0} · domain ${bd.domain || 0} · tasks ${bd.responsibilities || 0} · location ${bd.location || 0}`
    + (bd.sponsor ? ` · sponsor +${bd.sponsor}` : "")];
  if ((job.why || []).length) lines.push("", "<b>Evidence</b>", ...job.why.map((w) => `• ${esc(w)}`));
  (job.sp || []).forEach((hit, i) => {
    let note = `${hit.icon || "🛂"} ${esc(hit.label)}: ${esc(hit.name)}`;
    if (hit.positions) note += ` · ${hit.positions} approved positions`;
    if ((hit.occupations || []).length) note += ` · hired ${esc(hit.occupations.join(", "))}`;
    if (hit.match === "variant") note += " (name variant)";
    if (i === 0) lines.push("");
    lines.push(note);
  });
  if (job.ai) {
    lines.push("", `<b>AI second opinion</b> · fit ${job.ai.fit}/10`);
    if (job.ai.summary) lines.push(`💡 ${esc(job.ai.summary)}`);
    if (job.ai.concerns) lines.push(`⚠️ ${esc(job.ai.concerns)}`);
    const facts = [];
    if (job.ai.years != null) facts.push(`${job.ai.years}+ years`);
    if (job.ai.sponsorship && job.ai.sponsorship !== "unknown") facts.push(`sponsorship ${job.ai.sponsorship.replace("_", " ")}`);
    if ((job.ai.languages || []).length) facts.push(`languages: ${job.ai.languages.join(", ")}`);
    if (facts.length) lines.push(esc(facts.join(" · ")));
    (job.ai.requirements || []).forEach((r) => lines.push(`• ${esc(r)}`));
  }
  if (job.learned && job.learned.adj) {
    lines.push("", `🧠 Learned from you: ${job.learned.adj > 0 ? "+" : ""}${job.learned.adj} points (${esc((job.learned.because || []).join(", "))})`);
  }
  if ((job.rej || []).length) lines.push("", `Rejected: ${esc(job.rej.join(", "))}`);
  if ((job.src || []).length) lines.push("", `Sources: ${esc(job.src.join(", "))}`);
  if (job.url) lines.push("", `<a href="${esc(job.url)}">Open posting</a>`);
  return [lines.join("\n")];
}

function viewAi(index, prefs, arg) {
  const limit = clampInt(arg, 8, 1, 25);
  const current = currentMatches(index, prefs, false);
  const reviewed = current.filter((j) => j.ai).sort((a, b) => b.ai.fit - a.ai.fit || b.s - a.s);
  if (!reviewed.length) {
    return [index.settings && index.settings.ai
      ? `🤖 No current match has an AI review yet (${current.length} waiting). Reviews are added during scraper runs. /run starts one.`
      : "🤖 The AI second opinion is off: the CLOUDFLARE_AI_TOKEN secret is not set for the scraper."];
  }
  const lines = ["🤖 <b>AI second opinion</b>", `<i>${reviewed.length} of ${current.length} current matches reviewed · best fit first</i>`];
  reviewed.slice(0, limit).forEach((job, i) => {
    const title = job.url ? `<a href="${esc(job.url)}">${esc(job.t)}</a>` : `<b>${esc(job.t)}</b>`;
    lines.push("", `${i + 1}. ${title}${job.c ? ` — ${esc(job.c)}` : ""}`, `   🎯 fit ${job.ai.fit}/10 · score ${job.s} · <code>${job.code}</code>`);
    if (job.ai.summary) lines.push(`   💡 ${esc(job.ai.summary)}`);
    if (job.ai.concerns) lines.push(`   ⚠️ ${esc(job.ai.concerns)}`);
  });
  lines.push("", "/why code shows the full review · /pitch code drafts an application");
  return [lines.join("\n").slice(0, 4090)];
}

function viewSponsors(index, prefs, arg) {
  const limit = clampInt(arg, 10, 1, 30);
  const rows = currentMatches(index, prefs, false)
    .filter((j) => (j.sp || []).length || j.offered || (j.ai && j.ai.sponsorship === "offered"))
    .map((j) => {
      const offered = j.offered || (j.ai && j.ai.sponsorship === "offered");
      const local = (j.sp || []).some((h) => h.country === j.country);
      return { job: j, offered, rank: offered ? 0 : local ? 1 : 2 };
    }).sort((a, b) => a.rank - b.rank || b.job.s - a.job.s);
  if (!rows.length) return ["No current match comes from an employer on the UK, Canada or Netherlands sponsor registers yet, and none states that it sponsors."];
  const lines = ["🛂 <b>Sponsorship evidence</b>", `<i>${rows.length} current matches · postings that say so first, then employers licensed in the job's own country</i>`];
  rows.slice(0, limit).forEach(({ job, offered }, i) => {
    const title = job.url ? `<a href="${esc(job.url)}">${esc(job.t)}</a>` : `<b>${esc(job.t)}</b>`;
    lines.push("", `${i + 1}. ${title}${job.c ? ` — ${esc(job.c)}` : ""}`, `   score ${job.s} · ${esc(job.country || job.loc || "location unknown")} · <code>${job.code}</code>`);
    if (offered) lines.push("   ✅ the posting offers visa sponsorship");
    (job.sp || []).slice(0, 3).forEach((hit) => {
      let detail = `   ${hit.icon || "🛂"} ${esc(hit.label)}: ${esc(hit.name)}`;
      if (hit.positions) detail += ` · ${hit.positions} approved positions`;
      if ((hit.occupations || []).length) detail += ` · hired ${esc(hit.occupations.join(", "))}`;
      if (hit.match === "variant") detail += " (name variant: check)";
      lines.push(detail);
    });
  });
  return [lines.join("\n").slice(0, 4090)];
}

function viewStatus(index, prefs) {
  const run = index.run || {};
  const settings = index.settings || {};
  const current = currentMatches(index, prefs, false);
  const reviewed = current.filter((j) => j.ai).length;
  const interns = prefs.exclude_internships != null ? prefs.exclude_internships : settings.exclude_internships;
  const possible = prefs.notify_possible != null ? prefs.notify_possible : settings.notify_possible;
  return [[
    "<b>Status</b>",
    `Last run: ${esc(run.started_at || "never")} · code ${esc(run.code || "?")}`,
    `That run: ${run.high || 0} high · ${run.possible || 0} possible · ${run.alerted || 0} alerted · ${run.new || 0} new`,
    `Stored jobs: ${run.jobs_in_state || 0} · fresh matches now: ${current.length}`,
    `AI second opinion: ${settings.ai ? `${reviewed} of ${current.length} current matches reviewed` : "off"}`,
    `Failed sources: ${esc((run.failed || []).join(", ")) || "none"}`,
    `Thresholds: High ≥ ${prefs.high_threshold || settings.high}, Possible ≥ ${prefs.medium_threshold || settings.medium}`,
    `Alerts: ${prefs.paused ? "paused" : "on"} (${possible ? "High and Possible" : "High only"}) · internships ${interns ? "excluded" : "included"}`,
    `Muted: ${esc(prefs.muted.join(", ")) || "nothing"} · applied: ${Object.keys(prefs.applied).length} · hidden: ${prefs.hidden.length}`,
    `<i>answered instantly from the index of ${esc(index.generated_at || "?")}</i>`,
  ].join("\n")];
}

async function viewApplied(prefs) {
  const items = Object.entries(prefs.applied);
  if (!items.length) return ["No applications recorded yet. /applied code marks one."];
  items.sort((a, b) => String(b[1].at || "").localeCompare(String(a[1].at || "")));
  const tally = {};
  items.forEach(([, a]) => { const s = a.status || "applied"; tally[s] = (tally[s] || 0) + 1; });
  const lines = ["<b>Your applications</b>",
    `<i>${Object.entries(tally).map(([s, n]) => `${STATUSES[s] || "•"} ${n} ${s}`).join(" · ")}</i>`, ""];
  for (const [id, a] of items.slice(0, 40)) {
    const status = a.status || "applied";
    lines.push(`${STATUSES[status] || "•"} ${esc(a.title)}${a.company ? ` — ${esc(a.company)}` : ""} · ${status} · ${String(a.at || "").slice(0, 10)} · <code>${await codeOf(id)}</code>`);
  }
  lines.push("", "/outcome code interview|offer|rejected|withdrawn|ghosted updates one");
  return [lines.join("\n")];
}

function viewSignals(index, arg) {
  const items = (index.signals || []).slice(0, clampInt(arg, 10, 1, 20));
  if (!items.length) return ["No procurement signals stored yet. They are checked once a day during scraper runs."];
  const labels = { consultancy: "🧑‍💼 <b>Individual consultancies</b>", award: "🏆 <b>Firms that just won geospatial contracts</b>", tender: "📄 <b>Projects being tendered</b>" };
  const order = { consultancy: 0, award: 1, tender: 2 };
  const lines = ["🛰 <b>Recent market signals</b>", "<i>World Bank-financed procurement, geospatial work only</i>"];
  let kind = null;
  [...items].sort((a, b) => order[a.kind] - order[b.kind]).forEach((s) => {
    if (s.kind !== kind) { kind = s.kind; lines.push("", labels[kind] || kind); }
    let line = `• <a href="${esc(s.url)}">${esc(s.title)}</a> — ${esc(s.country || "?")}`;
    if (s.winner) line += `\n   won by <b>${esc(s.winner)}</b>${s.winner_country ? ` (${esc(s.winner_country)})` : ""}${s.value ? ` · ${esc(s.value)}` : ""}`;
    lines.push(line);
  });
  return [lines.join("\n").slice(0, 4090)];
}

/** Commands that change preferences. Returns the reply, or null to let Python handle the message. */
async function mutate(env, index, prefs, command, arg) {
  const now = new Date().toISOString();
  const onOff = arg.trim().toLowerCase();
  let answer = null;
  if (command === "pause") { prefs.paused = true; answer = "⏸ Alerts paused. Jobs keep being collected; /resume releases them."; }
  else if (command === "resume") { prefs.paused = false; answer = "▶️ Alerts resumed."; }
  else if (command === "interns" && (onOff === "on" || onOff === "off")) {
    prefs.exclude_internships = onOff === "off";
    answer = `Internships will be ${onOff === "off" ? "excluded" : "included"}. Applies from the next run.`;
  } else if (command === "possible" && (onOff === "on" || onOff === "off")) {
    prefs.notify_possible = onOff === "on";
    answer = `Alerts will include ${onOff === "on" ? "High and Possible matches." : "High matches only. /jobs and /range still show the Possible ones."}`;
  } else if (command === "mute" && arg) {
    if (!prefs.muted.some((t) => fold(t) === fold(arg))) prefs.muted.push(arg);
    answer = `🔇 Muted “${esc(arg)}”. /muted shows the list.`;
  } else if (command === "unmute" && arg) {
    const before = prefs.muted.length;
    prefs.muted = prefs.muted.filter((t) => fold(t) !== fold(arg));
    if (prefs.muted.length === before) return [`“${esc(arg)}” wasn't muted.`];
    answer = `🔊 Unmuted “${esc(arg)}”.`;
  } else if (command === "threshold") {
    const numbers = (arg.match(/\d{1,3}/g) || []).map(Number).filter((n) => n > 0 && n <= 100);
    if (!numbers.length) return [`Usage: /threshold 70 55 (now High ≥ ${prefs.high_threshold || index.settings.high}, Possible ≥ ${prefs.medium_threshold || index.settings.medium})`];
    prefs.high_threshold = numbers[0];
    prefs.medium_threshold = Math.min(numbers.length > 1 ? numbers[1] : (prefs.medium_threshold || index.settings.medium), numbers[0]);
    answer = `Thresholds set: High ≥ ${prefs.high_threshold}, Possible ≥ ${prefs.medium_threshold}. They apply from the next run.`;
  } else if (command === "locations" && arg) {
    prefs.preferred_locations = onOff === "reset" ? null : arg.split(",").map((p) => p.trim()).filter(Boolean);
    answer = onOff === "reset" ? "Preferred locations reset to the default." : `Preferred locations set to ${esc(prefs.preferred_locations.join(", "))}.`;
  } else if (command === "hide" || command === "unhide") {
    const job = findByCode(index, arg);
    if (!job) return [`Usage: /${command} code`];
    if (command === "hide") {
      if (!prefs.hidden.includes(job.id)) { prefs.hidden.push(job.id); prefs.hidden_info[job.id] = { ...snapshot(job), at: now }; }
      answer = `🙈 Hidden: ${esc(job.t)}. /unhide ${job.code} restores it.`;
    } else {
      if (!prefs.hidden.includes(job.id)) return ["That job isn't hidden."];
      prefs.hidden = prefs.hidden.filter((id) => id !== job.id);
      delete prefs.hidden_info[job.id];
      answer = `Restored: ${esc(job.t)}`;
    }
  } else if (command === "applied" && arg) {
    const job = findByCode(index, arg);
    if (!job) return ["I can't find that code. /jobs lists current codes."];
    prefs.applied[job.id] = { ...snapshot(job), url: job.url, at: now, status: "applied", history: [{ at: now, status: "applied" }] };
    answer = `✅ Marked as applied: <b>${esc(job.t)}</b>. Good luck! I'll check in with you in a week; tell me how it goes with /outcome ${job.code} interview|rejected|offer.`;
  } else if (command === "outcome") {
    const words = arg.split(/\s+/).filter(Boolean);
    const status = words.map((w) => w.toLowerCase()).find((w) => w in STATUSES);
    const code = words.find((w) => !(w.toLowerCase() in STATUSES)) || "";
    const job = findByCode(index, code);
    let id = job ? job.id : undefined;
    if (!id) {  // applied long ago and no longer in the index: match the code Python derives from the id
      for (const key of Object.keys(prefs.applied)) {
        if (key.toLowerCase() === code.toLowerCase() || await codeOf(key) === code.toLowerCase()) { id = key; break; }
      }
    }
    if (!status || !id) return ["Usage: /outcome code interview|offer|rejected|withdrawn|ghosted — /applied lists your codes."];
    if (!(id in prefs.applied) && job) prefs.applied[id] = { ...snapshot(job), url: job.url, at: now, status: "applied", history: [{ at: now, status: "applied" }] };
    const info = prefs.applied[id];
    info.status = status;
    (info.history = info.history || []).push({ at: now, status });
    const cheer = { interview: "🎤 An interview! Well done.", offer: "🎉 An offer! Congratulations.", rejected: "❌ Noted. Their loss; on to the next.",
      withdrawn: "↩️ Noted as withdrawn.", ghosted: "👻 Noted as no reply.", applied: "📨 Back to 'applied'." }[status];
    answer = `${cheer}\n<b>${esc(info.title)}</b>${info.company ? ` — ${esc(info.company)}` : ""}`;
  }
  if (answer === null) return null;
  await savePrefs(env, prefs);
  return [answer];
}

/** Try to answer from R2. Returns true when the message was handled here. */
async function answerFast(env, command, arg, echo) {
  if (!env.INBOX || !(FAST_READ.has(command) || FAST_WRITE.has(command))) return false;
  const index = await readJson(env, INDEX_KEY);
  if (!index || !Array.isArray(index.jobs)) return false;  // no index yet: Python answers as before
  const prefs = await loadPrefs(env);
  let messages;
  if (command === "help" || command === "start") messages = [index.help || "Send /jobs, /status, /why code…"];
  else if (command === "jobs" || command === "top" || command === "high") {
    const limit = clampInt(arg, 10, 1, 40);
    const rows = currentMatches(index, prefs, command === "high");
    messages = listing(rows.slice(0, limit), `Top ${Math.min(limit, rows.length)} of ${rows.length} ${command === "high" ? "High matches" : "current matches"}`,
      "Nothing relevant and fresh is stored right now. /status shows the last run.");
  } else if (command === "range") {
    const numbers = (arg.match(/\d{1,3}/g) || []).map(Number).filter((n) => n <= 100);
    if (!numbers.length) messages = ["Usage: /range 60 70 — or just say “jobs between 60 and 70”, “jobs above 80”."];
    else {
      const low = numbers.length > 1 ? Math.min(numbers[0], numbers[1]) : numbers[0];
      const high = numbers.length > 1 ? Math.max(numbers[0], numbers[1]) : 100;
      const now = Date.now();
      const rows = index.jobs.filter((j) => j.s >= low && j.s <= high && isCurrent(j, index, prefs, now)).sort((a, b) => b.s - a.s);
      messages = listing(rows.slice(0, 30), `${rows.length} job${rows.length === 1 ? "" : "s"} scoring ${low}–${high}`, `No fresh job scores between ${low} and ${high}.`);
    }
  } else if (command === "search") {
    const words = fold(arg).split(" ").filter(Boolean);
    if (!words.length) messages = ["Usage: /search lidar toronto"];
    else {
      const rows = index.jobs.filter((j) => (j.tier === "high" || j.tier === "possible")
        && words.every((w) => fold([j.t, j.c, j.loc, j.country, (j.sk || []).join(" "), (j.dom || []).join(" ")].join(" ")).includes(w)))
        .sort((a, b) => b.s - a.s);
      messages = listing(rows.slice(0, 15), `${rows.length} stored match${rows.length === 1 ? "" : "es"} for “${arg}”`, `No stored match for “${esc(arg)}”.`);
    }
  } else if (command === "why") messages = viewWhy(index, arg);
  else if (command === "ai") messages = viewAi(index, prefs, arg);
  else if (command === "sponsors" || command === "sponsor") messages = viewSponsors(index, prefs, arg);
  else if (command === "status") messages = viewStatus(index, prefs);
  else if (command === "muted") messages = [`Muted: ${esc(prefs.muted.join(", ")) || "nothing"}`];
  else if (command === "signals") messages = viewSignals(index, arg);
  else if (command === "applied" && !arg.trim()) messages = await viewApplied(prefs);
  else messages = await mutate(env, index, prefs, command, arg.trim());
  if (!messages) return false;
  if (echo) messages[0] = `${echo}\n\n${messages[0]}`;
  await reply(env, messages);
  return true;
}

/** Obvious one- or two-word messages that need neither the AI nor Python. */
function shortcut(text, index) {
  const t = fold(text).replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();
  let m;
  if (/^(status|statut|etat)$/.test(t)) return ["status", ""];
  if (/^(help|aide|commands|commandes)$/.test(t)) return ["help", ""];
  if ((m = t.match(/^(?:top|best|jobs?|offers?|offres?|latest|show jobs|top jobs|best jobs)\s*(\d{1,2})?$/))) return ["jobs", m[1] || ""];
  if ((m = t.match(/^(?:high|hautes?|high matches)\s*(\d{1,2})?$/))) return ["high", m[1] || ""];
  if (/^(ai|ia|ai summary|second opinion)$/.test(t)) return ["ai", ""];
  if (/^(sponsors?|visa|visas)$/.test(t)) return ["sponsors", ""];
  if (/^(signals?|tenders?)$/.test(t)) return ["signals", ""];
  if (/^[0-9a-f]{5}$/.test(t) && index && index.jobs.some((j) => j.code === t)) return ["why", t];
  return null;
}

// ---------------------------------------------------------------------------- hand-over to Python
async function dispatchToPython(env, update, text, hint) {
  await tell(env, "⏳ On it. Reply in about a minute.");
  let inputs;
  if (env.INBOX) {  // private hand-over: nothing about the message appears in the public workflow run
    const key = keyOf(env, `inbox/${String(update.update_id).padStart(12, "0")}.json`);
    await env.INBOX.put(key, JSON.stringify({ text, hint, at: new Date().toISOString() }),
      { httpMetadata: { contentType: "application/json" } });
    inputs = { inbox: "true" };
  } else {
    inputs = { text, hint };
  }
  const dispatch = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/commands.yml/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "geospatial-job-bot-worker",
      },
      body: JSON.stringify({ ref: "main", inputs }),
    },
  );
  if (!dispatch.ok) {
    await tell(env, `⚠️ I could not start the command (GitHub answered ${dispatch.status}). Check the Worker's GITHUB_TOKEN.`);
  }
}

async function handleMessage(env, update, text) {
  // 1. explicit commands
  if (text.startsWith("/")) {
    const [head, ...rest] = text.split(/\s+/);
    const command = head.slice(1).split("@")[0].toLowerCase();
    if (await answerFast(env, command, rest.join(" "), "")) return;
    return dispatchToPython(env, update, text, "");
  }
  // 2. obvious words
  if (env.INBOX) {
    const index = await readJson(env, INDEX_KEY);
    const quick = shortcut(text, index);
    if (quick && await answerFast(env, quick[0], quick[1], `↪ <i>/${quick[0]}${quick[1] ? ` ${esc(quick[1])}` : ""}</i>`)) return;
  }
  // 3. free text: ask the AI (bounded); alone it may only open read-only views
  let hint = "";
  if (env.AI) {
    let timer;
    try {
      hint = await Promise.race([aiHint(env, text), new Promise((resolve) => { timer = setTimeout(() => resolve(""), AI_TIMEOUT_MS); })]);
    } catch { hint = ""; }
    clearTimeout(timer);
  }
  if (hint) {
    const [head, ...rest] = hint.split(/\s+/);
    const command = head.slice(1);
    if (FAST_READ.has(command) && await answerFast(env, command, rest.join(" "), `↪ <i>${esc(hint)} · AI</i>`)) return;
  }
  // 4. everything else: the Python rules decide
  return dispatchToPython(env, update, text, hint);
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("geospatial job bot webhook");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    let update;
    try { update = await request.json(); } catch { return new Response("bad request", { status: 400 }); }
    // Telegram delivers text in different envelopes depending on the chat: "message" (private chats and groups),
    // "channel_post" (channels), "business_message", and the edited_* variants. Accept them all.
    const message = update && (update.message || update.channel_post || update.business_message
      || update.edited_message || update.edited_channel_post);
    const raw = message && (typeof message.text === "string" ? message.text : message.caption);
    const text = typeof raw === "string" ? raw.trim().slice(0, 500) : "";
    // Setup helper, answered in the chat it was asked in and before any authorisation: "/id" tells you the values to
    // use for TELEGRAM_CHAT_ID (this chat) and TELEGRAM_OWNER_ID (you). It reveals nothing but the asker's own ids.
    if (message && message.chat && /^\/id(@\w+)?$/i.test(text)) {
      const configured = String(message.chat.id) === String(env.TELEGRAM_CHAT_ID);
      const lines = [
        `chat id: ${message.chat.id}`,
        `chat type: ${message.chat.type}`,
        `your user id: ${message.from ? message.from.id : "unknown (anonymous admin or channel post)"}`,
        configured ? "✅ This chat is the configured TELEGRAM_CHAT_ID." : "ℹ️ This chat is NOT the configured TELEGRAM_CHAT_ID.",
        `instant replies: ${env.INBOX ? "on (R2 bound)" : "off (add the INBOX R2 binding)"} · AI: ${env.AI ? "on" : "off"}`,
      ];
      ctx.waitUntil(telegram(env, message.chat.id, lines.join("\n"), false));
      return new Response("ok");
    }
    // Authorised when the message is in the configured chat, or is written by the owner anywhere (a private chat id
    // is also that person's user id). TELEGRAM_OWNER_ID can name the owner explicitly when the chat is a group.
    const owner = String(env.TELEGRAM_OWNER_ID || env.TELEGRAM_CHAT_ID);
    const inChat = Boolean(message && message.chat) && String(message.chat.id) === String(env.TELEGRAM_CHAT_ID);
    const fromOwner = Boolean(message && message.from) && !message.from.is_bot && String(message.from.id) === owner;
    if (!text || !(inChat || fromOwner)) {
      // Visible in the Worker's Observability logs; says why without revealing the message.
      console.log("ignored update", JSON.stringify({
        envelope: update ? Object.keys(update).filter((k) => k !== "update_id") : [],
        chatType: message && message.chat ? message.chat.type : null,
        chatMatches: inChat || fromOwner,
        hasText: Boolean(text),
        fields: message ? Object.keys(message) : [],
      }));
      return new Response("ignored");
    }
    console.log("accepted update", JSON.stringify({
      chatType: message.chat.type, via: inChat ? "chat" : "owner", command: text.startsWith("/"),
      ai: Boolean(env.AI), inbox: Boolean(env.INBOX),
    }));
    // Answer Telegram at once (a slow reply makes it retry the update) and finish the work in the background.
    // Whatever goes wrong is reported to the chat instead of failing silently.
    ctx.waitUntil(
      handleMessage(env, update, text).catch((error) =>
        tell(env, `⚠️ Worker error: ${String(error && error.message ? error.message : error).slice(0, 300)}`)),
    );
    return new Response("ok");
  },
};
