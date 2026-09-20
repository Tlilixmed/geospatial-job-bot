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
 *   DASHBOARD_KEY        optional: 16+ random characters; enables the read-only page at /dash/<DASHBOARD_KEY>
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
  "outcome", "possible", "radar", "skills", "signals", "learning", "sources", "visa", "watch", "unwatch", "prospects", "prep", "approach", "ask"];
const HINT_RE = new RegExp(`^/(${COMMANDS.join("|")})(\\s[^\\n]{0,200})?$`);
const FAST_READ = new Set(["jobs", "top", "high", "range", "search", "why", "ai", "sponsors", "sponsor", "status", "help",
  "start", "muted", "signals", "sources", "yield", "visa", "prospects"]);
const VIEW_ALIASES = { yield: "sources" };  // replies Python formatted in advance: index.views[command]
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
/prep CODE           prepare for an interview for that job: likely questions, weak points
/approach FIRM       write an unsolicited application to a firm (no job posted)
/ask QUESTION        a question that needs comparing or reasoning over the matches ("which pay best", "compare a and b",
                     "which close this week", "which suit me if I only speak French") - repeat the question after /ask
/ai [n]              show the AI's opinion (fit, summary, concerns) of the current matches
/sponsors [n]        jobs from employers on official visa-sponsor registers, or that offer sponsorship
/visa [CODE|COUNTRY] is a work visa realistic: salary minimum, licence, occupation; or a country's visa rules
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
/sources             which job sources deliver results and which are noise
/watch COMPANY       follow an employer closely          /unwatch COMPANY   /watch  list them
/prospects           employers proven to sponsor whose job boards the bot found
/learning            what the bot learned from the user's applications and hidden jobs
/run                 search for new jobs right now
/help                what the bot can do
If the message simply looks for jobs of some kind, use /search. If nothing fits, answer /help.`;

// ---------------------------------------------------------------------------- small helpers
const esc = (value) => String(value == null ? "" : value)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const fold = (value) => String(value || "").normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase()
  .replace(/\s+/g, " ").trim();
/** "75.5 60" -> [76, 60]: a decimal is one number, not two ("75.5" once stored Possible >= 5). */
const wholeNumbers = (text) => (String(text || "").match(/\d+(?:\.\d+)?/g) || []).map((n) => Math.round(parseFloat(n)));
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
    for (const part of splitMessage(message)) {
      const response = await telegram(env, env.TELEGRAM_CHAT_ID, part, true);
      if (!response.ok) await tell(env, part.replace(/<[^>]+>/g, "").slice(0, 4000));  // bad markup: send it plain
    }
  }
}

/** Keys follow the bot's STATE_PREFIX (a plain Worker variable, only needed when the GitHub side sets one). */
const keyOf = (env, suffix) => {
  const prefix = String(env.STATE_PREFIX || "").replace(/^[/]+|[/]+$/g, "");
  return prefix ? `${prefix}/${suffix}` : suffix;
};

/** Whole words only: muting "US" must not silence "Industry" (same rule as geojobbot.core.jobs.is_muted). */
function mutedBy(haystack, term) {
  const wanted = fold(term);
  if (!wanted) return false;
  for (let from = 0; ;) {
    const at = haystack.indexOf(wanted, from);
    if (at === -1) return false;
    const before = at === 0 ? " " : haystack[at - 1];
    const after = haystack[at + wanted.length] || " ";
    if (!/[a-z0-9]/.test(before) && !/[a-z0-9]/.test(after)) return true;
    from = at + 1;
  }
}

/** Long replies are cut between blocks (blank lines), never through a tag: a broken tag makes Telegram refuse the message. */
function splitMessage(text) {
  if (text.length <= MAX_MESSAGE) return [text];
  const parts = [];
  let current = "";
  const push = (block, glue) => {
    if (current && current.length + glue.length + block.length > MAX_MESSAGE) { parts.push(current); current = ""; }
    current += (current ? glue : "") + block;
  };
  for (const block of text.split("\n\n")) {
    if (block.length <= MAX_MESSAGE) { push(block, "\n\n"); continue; }
    for (const line of block.split("\n")) push(line.length <= MAX_MESSAGE ? line : `${line.replace(/<[^>]+>/g, "").slice(0, MAX_MESSAGE - 1)}…`, "\n");
  }
  if (current) parts.push(current);
  return parts;
}

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
  const object = await env.INBOX.get(keyOf(env, PREFS_KEY));
  let stored = null;
  if (object) {
    // a file that exists but cannot be read must never be replaced by defaults: that would erase every application
    try { stored = await object.json(); } catch { throw new Error("your preferences could not be read, so nothing was changed"); }
  }
  const prefs = defaultPrefs();
  // the version this copy was read at: saving is refused when someone else (the Python side) wrote in between
  Object.defineProperty(prefs, "_etag", { value: object && object.etag ? object.etag : null, enumerable: false });
  // keep keys this Worker does not know (newer Python versions add some): saving must never drop them
  if (stored && typeof stored === "object") for (const key of Object.keys(stored)) if (stored[key] !== undefined) prefs[key] = stored[key];
  for (const key of ["muted", "hidden"]) if (!Array.isArray(prefs[key])) prefs[key] = [];
  for (const key of ["applied", "hidden_info"]) if (!prefs[key] || typeof prefs[key] !== "object") prefs[key] = {};
  return prefs;
}
/** false when the stored file changed since it was read (R2 conditional put): the caller reloads and tries again. */
async function savePrefs(env, prefs) {
  const options = { httpMetadata: { contentType: "application/json" } };
  if (prefs._etag) options.onlyIf = { etagMatches: prefs._etag };
  const saved = await env.INBOX.put(keyOf(env, PREFS_KEY), JSON.stringify({ ...prefs, updated_at: new Date().toISOString() }, null, 2), options);
  return saved !== null;
}
const CONFLICT = Symbol("preferences changed meanwhile");

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
  if (job.seen && now - Date.parse(job.seen) > (job.ttl || (job.rot ? 21 : 5)) * 864e5) return false;
  if (job.dl && Date.parse(job.dl) + 864e5 < now) return false;  // the application deadline has passed
  if (prefs.hidden.includes(job.id) || Object.hasOwn(prefs.applied, job.id)) return false;
  const haystack = fold(`${job.t || ""} ${job.c || ""}`);
  return !prefs.muted.some((term) => mutedBy(haystack, term));
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

function deadlineFact(job) {
  if (!job.dl) return "";
  const days = Math.floor((Date.parse(job.dl) + 864e5 - Date.now()) / 864e5);
  if (days < 0) return `⌛ the application deadline passed on ${job.dl}`;
  return `⏳ closes ${days === 0 ? "today" : days === 1 ? "tomorrow" : days <= 10 ? `in ${days} days` : job.dl}`;
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
  if (job.dl) {
    const days = Math.floor((Date.parse(job.dl) + 864e5 - Date.now()) / 864e5);
    if (days >= 0) facts.push(`⏳ closes ${days === 0 ? "today" : days === 1 ? "tomorrow" : days <= 10 ? `in ${days} days` : job.dl}`);
  }
  if (job.sal) facts.push(`💰 ${esc(job.sal)}`);
  if (job.offered) facts.push("🛂 sponsorship offered");
  const badge = sponsorBadge(job);
  if (badge) facts.push(esc(badge));
  if (job.w) facts.push("👀 watched employer");
  if (job.rp) facts.push(esc(job.rp));
  const lines = [head, `   ${facts.join(" · ")}`];
  if (job.ai && job.ai.summary) lines.push(`   💡 ${esc(job.ai.summary)}`);
  if (job.ai && job.ai.concerns) lines.push(`   ⚠️ ${esc(job.ai.concerns)}`);
  if (job.visa && job.visa.b) lines.push(`   ${esc(job.visa.b)}`);
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
  if (!job) return arg.trim() ? null : ["Usage: /why code — the 5-character tag next to a job."];  // older job: Python has the full state
  const bd = job.bd || {};
  const lines = [`<b>${esc(job.t)}</b>${job.c ? ` — ${esc(job.c)}` : ""}`, `Score ${job.s}/100 · ${esc(job.tier)}`,
    `Title ${bd.title || 0} · skills ${bd.tech || 0} · domain ${bd.domain || 0} · tasks ${bd.responsibilities || 0} · location ${bd.location || 0}`];
  const extras = [["sponsor", "sponsor register"], ["learned", "learned"], ["visa", "visa route"], ["ai", "AI reading"]]
    .filter(([key]) => bd[key]).map(([key, label]) => `${label} ${bd[key] > 0 ? "+" : ""}${bd[key]}`);
  if (extras.length) lines.push(`Adjustments: ${extras.join(" · ")}`);
  if ((job.why || []).length) lines.push("", "<b>Evidence</b>", ...job.why.map((w) => `• ${esc(w)}`));
  (job.sp || []).forEach((hit, i) => {
    let note = `${hit.icon || "🛂"} ${esc(hit.label)}: ${esc(hit.name)}`;
    if (hit.positions) note += ` · ${hit.positions} foreign hires approved`;
    if ((hit.occupations || []).length) note += ` · hired ${esc(hit.occupations.join(", "))}`;
    if (hit.match === "variant") note += " (a similar name: check the register)";
    if (i === 0) lines.push("");
    lines.push(note);
  });
  const facts = [job.gap, deadlineFact(job), job.rp].filter(Boolean);  // skills gap, deadline, reposts: the same line Python shows
  if (facts.length) lines.push("", esc(facts.join(" · ")));
  if (job.visa && (job.visa.d || []).length) lines.push("", ...job.visa.d);  // formatted by Python (HTML)
  if (job.ai) {
    lines.push("", `<b>AI second opinion</b> · fit ${job.ai.fit}/10${job.ai.model ? ` · ${esc(job.ai.model)}` : ""}`);
    if (job.ai.lift) lines.push("⤴️ The rules had passed on this one; the reading brought it back.");
    if (job.ai.restricted) lines.push("🚫 The reading found a citizenship, clearance or work-rights restriction.");
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
  return [lines.join("\n")];
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
      if (hit.positions) detail += ` · ${hit.positions} foreign hires approved`;
      if ((hit.occupations || []).length) detail += ` · hired ${esc(hit.occupations.join(", "))}`;
      if (hit.match === "variant") detail += " (name variant: check)";
      lines.push(detail);
    });
  });
  return [lines.join("\n")];
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
  const lines = ["🛰 <b>Recent market signals</b>", "<i>World Bank-financed procurement and EU public tenders, geospatial work only</i>"];
  let kind = null;
  [...items].sort((a, b) => order[a.kind] - order[b.kind]).forEach((s) => {
    if (s.kind !== kind) { kind = s.kind; lines.push("", labels[kind] || kind); }
    let line = `• <a href="${esc(s.url)}">${esc(s.title)}</a> — ${esc(s.country || "?")}`;
    if (s.winner) line += `\n   won by <b>${esc(s.winner)}</b>${s.winner_country ? ` (${esc(s.winner_country)})` : ""}${s.value ? ` · ${esc(s.value)}` : ""}`;
    lines.push(line);
  });
  return [lines.join("\n")];
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
    const numbers = wholeNumbers(arg).filter((n) => n > 0 && n <= 100);
    if (!numbers.length) return [`Usage: /threshold 70 55 (now High ≥ ${prefs.high_threshold || index.settings.high}, Possible ≥ ${prefs.medium_threshold || index.settings.medium})`];
    prefs.high_threshold = numbers[0];
    prefs.medium_threshold = Math.min(numbers.length > 1 ? numbers[1] : (prefs.medium_threshold || index.settings.medium), numbers[0]);
    answer = `Thresholds set: High ≥ ${prefs.high_threshold}, Possible ≥ ${prefs.medium_threshold}. They apply from the next run.`;
  } else if (command === "locations" && arg) {
    prefs.preferred_locations = onOff === "reset" ? null : arg.split(",").map((p) => p.trim()).filter(Boolean);
    answer = onOff === "reset" ? "Preferred locations reset to the default." : `Preferred locations set to ${esc(prefs.preferred_locations.join(", "))}.`;
  } else if (command === "hide" || command === "unhide") {
    const job = findByCode(index, arg);
    if (!job) return arg ? null : [`Usage: /${command} code`];  // not in the index: Python looks in the full state
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
    if (!job) return null;  // not in the index: Python looks in the full state
    if (Object.hasOwn(prefs.applied, job.id)) {  // saying it twice must not wipe an interview already recorded
      const known = prefs.applied[job.id];
      return [`Already recorded: <b>${esc(job.t)}</b> · ${esc(known.status || "applied")} since ${esc(String(known.at || "").slice(0, 10))}. /outcome ${job.code} interview|offer|rejected updates it.`];
    }
    prefs.applied[job.id] = { ...snapshot(job), url: job.url, at: now, status: "applied", history: [{ at: now, status: "applied" }] };
    answer = `✅ Marked as applied: <b>${esc(job.t)}</b>. Good luck! I'll check in with you in a week; tell me how it goes with /outcome ${job.code} interview|rejected|offer.`;
  } else if (command === "outcome") {
    const words = arg.split(/\s+/).filter(Boolean);
    const status = words.map((w) => w.toLowerCase()).find((w) => Object.hasOwn(STATUSES, w));  // "constructor" is not a status
    const code = words.find((w) => !Object.hasOwn(STATUSES, w.toLowerCase())) || "";
    const job = findByCode(index, code);
    let id = job ? job.id : undefined;
    if (!id) {  // applied long ago and no longer in the index: match the code Python derives from the id
      for (const key of Object.keys(prefs.applied)) {
        if (key.toLowerCase() === code.toLowerCase() || await codeOf(key) === code.toLowerCase()) { id = key; break; }
      }
    }
    if (!status || !id) return ["Usage: /outcome code interview|offer|rejected|withdrawn|ghosted — /applied lists your codes."];
    if (!Object.hasOwn(prefs.applied, id) && job) prefs.applied[id] = { ...snapshot(job), url: job.url, at: now, status: "applied", history: [{ at: now, status: "applied" }] };
    const info = prefs.applied[id];
    info.status = status;
    (info.history = info.history || []).push({ at: now, status });
    const cheer = { interview: "🎤 An interview! Well done.", offer: "🎉 An offer! Congratulations.", rejected: "❌ Noted. Their loss; on to the next.",
      withdrawn: "↩️ Noted as withdrawn.", ghosted: "👻 Noted as no reply.", applied: "📨 Back to 'applied'." }[status];
    answer = `${cheer}\n<b>${esc(info.title)}</b>${info.company ? ` — ${esc(info.company)}` : ""}`;
  }
  if (answer === null) return null;
  if (!(await savePrefs(env, prefs))) return CONFLICT;
  return [answer];
}

/** Try to answer from R2. Returns true when the message was handled here. */
async function answerFast(env, command, arg, echo, update) {
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
    const numbers = wholeNumbers(arg).filter((n) => n <= 100);
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
  else if (command === "visa" && arg.trim() && !/^\d+$/.test(arg.trim())) {
    const job = findByCode(index, arg);
    const card = (index.visa_cards || {})[fold(arg)];
    if (job && job.visa && (job.visa.d || []).length) messages = [job.visa.d.join("\n")];
    else if (card) messages = [card];
    else return false;  // Python knows more spellings and explains the usage
  }
  else if ((index.views || {})[VIEW_ALIASES[command] || command]) messages = [index.views[VIEW_ALIASES[command] || command]];
  else if (command === "applied" && !arg.trim()) messages = await viewApplied(prefs);
  else {
    messages = await mutate(env, index, prefs, command, arg.trim());
    if (messages === CONFLICT) messages = await mutate(env, index, await loadPrefs(env), command, arg.trim());  // once more, on the fresh copy
    if (messages === CONFLICT) messages = ["⚠️ Your preferences were being changed at the same moment. Nothing was lost; please send that again."];
  }
  if (!messages) return false;
  if (echo) messages[0] = `${echo}\n\n${messages[0]}`;
  await reply(env, messages);
  // an interview was just recorded: the interview sheet needs the AI and the stored description, so Python writes it
  if (command === "outcome" && update && !update.edited && /\binterview\b/i.test(arg) && /^[🎤]/u.test(messages[0].replace(/^↪[^\n]*\n\n/, ""))) {
    const code = arg.split(/\s+/).find((w) => /^[0-9a-f]{5}$/i.test(w));
    if (code) await dispatchToPython(env, update, `/prep ${code.toLowerCase()}`, "", "🎤 Preparing your interview sheet, about a minute.");
  }
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
  if (/^sponsors?$/.test(t)) return ["sponsors", ""];
  if (/^(signals?|tenders?)$/.test(t)) return ["signals", ""];
  if (/^(sources?|yield)$/.test(t)) return ["sources", ""];
  if (/^visas?$/.test(t)) return ["visa", ""];
  if (/^[0-9a-f]{5}$/.test(t) && index && index.jobs.some((j) => j.code === t)) return ["why", t];
  return null;
}

// ---------------------------------------------------------------------------- hand-over to Python
async function dispatchToPython(env, update, text, hint, notice) {
  await tell(env, notice || "⏳ On it. Reply in about a minute.");
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
  // 0. an edited message may open a view again, but it never changes anything twice and never starts a workflow
  if (update.edited) {
    const [head, ...rest] = text.split(/\s+/);
    const command = text.startsWith("/") ? head.slice(1).split("@")[0].toLowerCase() : "";
    if (FAST_READ.has(command) && await answerFast(env, command, rest.join(" "), "", update)) return;
    return tell(env, "✏️ I saw the edit, but edited messages are not run again. Send it as a new message.");
  }
  // 1. explicit commands
  if (text.startsWith("/")) {
    const [head, ...rest] = text.split(/\s+/);
    const command = head.slice(1).split("@")[0].toLowerCase();
    if (await answerFast(env, command, rest.join(" "), "", update)) return;
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

// ---------------------------------------------------------------------------- private dashboard (read-only)
// GET /dash/<DASHBOARD_KEY> renders state/index.json and state/prefs.json. Off until the DASHBOARD_KEY secret exists.
// The page script uses no backticks, no "${" and no backslashes, so it can live in this template literal unchanged.
const DASHBOARD_PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Geospatial jobs</title><style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1a2330;--mute:#5d6b7c;--line:#dde3ea;--acc:#0b6bcb;--good:#1a7f4b;--warn:#b26a00;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#10151c;--card:#182029;--ink:#e6ebf1;--mute:#93a1b2;--line:#2a3542;--acc:#6db3ff;--good:#5fd39a;--warn:#f0b35a;--bad:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,Segoe UI,Roboto,sans-serif}
header{padding:16px;max-width:1100px;margin:auto}h1{font-size:20px;margin:0 0 4px}small,.mute{color:var(--mute)}
nav{display:flex;gap:6px;flex-wrap:wrap;padding:0 16px;max-width:1100px;margin:auto}
nav button{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:7px 12px;border-radius:18px;cursor:pointer;font:inherit}
nav button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
main{padding:16px;max-width:1100px;margin:auto}.bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
input,select{font:inherit;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);min-width:0}
input{flex:1 1 180px}.job,.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:10px}
.job h3{margin:0 0 4px;font-size:16px}.job a{color:var(--acc);text-decoration:none}.row{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
.chip{border:1px solid var(--line);border-radius:12px;padding:1px 8px;font-size:12.5px;color:var(--mute)}
.chip.good{color:var(--good);border-color:var(--good)}.chip.warn{color:var(--warn);border-color:var(--warn)}.chip.bad{color:var(--bad);border-color:var(--bad)}
.score{float:right;font-weight:700;font-size:18px}.score.high{color:var(--good)}.note{margin-top:6px;color:var(--mute);font-size:13.5px}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.cols h4{margin:0 0 8px}
.view{white-space:pre-wrap;overflow-wrap:anywhere}.view a{color:var(--acc)}code{background:var(--line);border-radius:4px;padding:0 4px}
</style></head><body><header><h1>Geospatial jobs</h1><small id="meta"></small></header>
<nav id="nav"></nav><main id="main"></main><script id="data" type="application/json">__DATA__</script><script>
(function(){
var D=JSON.parse(document.getElementById("data").textContent),I=D.index||{},P=D.prefs||{},jobs=I.jobs||[],now=Date.now();
var hidden={},applied=P.applied||{};(P.hidden||[]).forEach(function(id){hidden[id]=1});
function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e}
function current(j){var s=I.settings||{};if(j.rel&&j.p){var lim=j.rot?s.rotation_max_age_h:s.max_age_h;if(lim&&(now-Date.parse(j.p))/36e5>lim)return false}
 if(j.seen&&now-Date.parse(j.seen)>(j.ttl||(j.rot?21:5))*864e5)return false;if(j.dl&&Date.parse(j.dl)+864e5<now)return false;return !hidden[j.id]&&!applied[j.id]}
function safe(url){return /^https?:/i.test(url||"")?url:"#"}
function chip(text,kind){return el("span","chip"+(kind?" "+kind:""),text)}
function jobCard(j){var c=el("div","job"),h=el("h3");c.appendChild(el("span","score"+(j.tier==="high"?" high":""),String(j.s)));
 var a=el("a",null,j.t||"");a.href=safe(j.url);a.target="_blank";a.rel="noopener noreferrer";h.appendChild(a);c.appendChild(h);
 c.appendChild(el("div","mute",[j.c,j.loc].filter(Boolean).join(" · ")));var r=el("div","row");r.appendChild(chip(j.code));
 if(j.sal)r.appendChild(chip(j.sal));if(j.dl)r.appendChild(chip("closes "+j.dl,"warn"));if(j.offered)r.appendChild(chip("sponsorship offered","good"));
 (j.sp||[]).slice(0,1).forEach(function(s){r.appendChild(chip(s.label,s.country===j.country?"good":""))});
 if(j.visa&&j.visa.v)r.appendChild(chip("visa: "+j.visa.v,j.visa.v==="strong"?"good":j.visa.v==="blocked"?"bad":j.visa.v==="hard"?"warn":""));
 if(j.w)r.appendChild(chip("watched","good"));if(j.rp)r.appendChild(chip(j.rp));if(j.ai)r.appendChild(chip("AI fit "+j.ai.fit+"/10"));
 (j.sk||[]).slice(0,4).forEach(function(s){r.appendChild(chip(s.split(" (")[0]))});c.appendChild(r);
 if(j.ai&&j.ai.summary)c.appendChild(el("div","note",j.ai.summary));if(j.ai&&j.ai.concerns)c.appendChild(el("div","note","⚠ "+j.ai.concerns));return c}
function matches(root){var bar=el("div","bar"),q=el("input"),tier=el("select"),visa=el("select"),sort=el("select"),list=el("div");q.placeholder="Filter: title, company, place, skill";
 [["","High and possible"],["high","High only"],["all","Everything stored"]].forEach(function(o){var x=el("option",null,o[1]);x.value=o[0];tier.appendChild(x)});
 [["","Any visa route"],["strong","Visa looks open"],["open","Visa possible"],["sponsor","Sponsor evidence"]].forEach(function(o){var x=el("option",null,o[1]);x.value=o[0];visa.appendChild(x)});
 [["s","Best score"],["p","Newest"],["dl","Closing soonest"]].forEach(function(o){var x=el("option",null,o[1]);x.value=o[0];sort.appendChild(x)});
 function draw(){var words=q.value.toLowerCase().split(" ").filter(Boolean),rows=jobs.filter(function(j){
  if(tier.value==="high"&&j.tier!=="high")return false;if(tier.value===""&&j.tier!=="high"&&j.tier!=="possible")return false;if(!current(j))return false;
  if(visa.value==="sponsor"&&!((j.sp||[]).length||j.offered))return false;if((visa.value==="strong"||visa.value==="open")&&!(j.visa&&j.visa.v===visa.value))return false;
  var hay=[j.t,j.c,j.loc,j.country,(j.sk||[]).join(" ")].join(" ").toLowerCase();return words.every(function(w){return hay.indexOf(w)>=0})});
  rows.sort(function(a,b){if(sort.value==="p")return String(b.p||"").localeCompare(String(a.p||""));if(sort.value==="dl")return String(a.dl||"9").localeCompare(String(b.dl||"9"));return b.s-a.s});
  list.textContent="";list.appendChild(el("div","mute",rows.length+" jobs"));rows.slice(0,150).forEach(function(j){list.appendChild(jobCard(j))})}
 [q,tier,visa,sort].forEach(function(x){x.addEventListener("input",draw);bar.appendChild(x)});root.appendChild(bar);root.appendChild(list);draw()}
function applications(root){var order=["applied","interview","offer","rejected","ghosted","withdrawn"],cols=el("div","cols"),by={};
 Object.keys(applied).forEach(function(id){var a=applied[id],s=a.status||"applied";(by[s]=by[s]||[]).push(a)});
 if(!Object.keys(applied).length){root.appendChild(el("div","card","No applications recorded yet. In Telegram: /applied code"));return}
 order.forEach(function(s){if(!by[s])return;var col=el("div","card");col.appendChild(el("h4",null,s+" ("+by[s].length+")"));
  by[s].sort(function(a,b){return String(b.at||"").localeCompare(String(a.at||""))}).forEach(function(a){var d=el("div","note"),l=el("a",null,a.title||"?");l.href=safe(a.url);l.target="_blank";l.rel="noopener noreferrer";
   d.appendChild(l);d.appendChild(document.createTextNode(" — "+(a.company||"?")+" · "+String(a.at||"").slice(0,10)));col.appendChild(d)});cols.appendChild(col)});root.appendChild(cols)}
function view(name){return function(root){var box=el("div","card view"),html=(I.views||{})[name];if(html){box.innerHTML=html;Array.prototype.forEach.call(box.querySelectorAll("a"),function(a){if(!/^https?:/i.test(a.getAttribute("href")||""))a.removeAttribute("href");a.target="_blank";a.rel="noopener noreferrer"})}else{box.textContent="Nothing yet: this view is published by the next scraper run."}root.appendChild(box)}}
function watch(root){var w=P.watch||[],box=el("div","card");box.appendChild(el("h4",null,"Employers you watch"));
 if(!w.length)box.appendChild(el("div","mute","None. In Telegram: /watch company"));w.forEach(function(x){box.appendChild(el("div","note",x.name+(x.url?" — "+x.url:"")))});root.appendChild(box);view("prospects")(root)}
var tabs=[["Matches",matches],["Applications",applications],["Visa routes",view("visa")],["Employers",watch],["Sources",view("sources")]],nav=document.getElementById("nav"),main=document.getElementById("main");
function open(i){main.textContent="";Array.prototype.forEach.call(nav.children,function(b,k){b.className=k===i?"on":""});tabs[i][1](main)}
tabs.forEach(function(t,i){var b=el("button",null,t[0]);b.addEventListener("click",function(){open(i)});nav.appendChild(b)});
var run=I.run||{};document.getElementById("meta").textContent="index of "+(I.generated_at||"?")+" · last run: "+(run.high||0)+" high, "+(run["new"]||0)+" new, "+(run.jobs_in_state||0)+" stored"+((run.failed||[]).length?" · failed: "+run.failed.join(", "):"")+" · read-only, act through Telegram";
open(0)})();
</script></body></html>`;

function sameKey(given, expected) {
  if (!expected || given.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < given.length; i += 1) diff |= given.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

async function dashboard(request, env) {
  const parts = new URL(request.url).pathname.split("/").filter(Boolean);
  if (parts[0] !== "dash") return null;
  const key = String(env.DASHBOARD_KEY || "");
  let given = "";
  try { given = decodeURIComponent(parts[1] || ""); } catch { given = ""; }  // "%zz" must look like any other wrong key
  if (key.length < 16 || !env.INBOX || !sameKey(given, key)) return new Response("not found", { status: 404 });
  const [index, prefs] = await Promise.all([readJson(env, INDEX_KEY), loadPrefs(env).catch(() => defaultPrefs())]);
  const data = JSON.stringify({ index: index || {}, prefs: { applied: prefs.applied, hidden: prefs.hidden, watch: prefs.watch || [] } })
    .replace(/</g, "\\u003c").replace(/[\u2028\u2029]/g, " ");
  return new Response(DASHBOARD_PAGE.replace("__DATA__", () => data), {
    headers: {
      "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow",
      "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
      "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
    },
  });
}

// ---------------------------------------------------------------------------- watchdog (Cron Trigger, optional)
// GitHub's scheduler is best-effort: runs are delayed, dropped, and schedules are disabled after 60 days without
// repository activity. With a Cron Trigger on this Worker (e.g. every hour) the bot notices when the scraper has been
// silent for too long, re-enables and starts the workflow itself, and says so once per incident.
const STALE_HOURS = 6;
const KICK_EVERY_HOURS = 3;
const MAX_KICKS = 4;
const WATCHDOG_KEY = "state/watchdog.json";

async function github(env, method, path, body) {
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${path}`, {
    method,
    headers: { Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "geospatial-job-bot-worker" },
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function watchdog(env) {
  if (!env.INBOX) return "no bucket";
  const index = await readJson(env, INDEX_KEY);
  const memo = (await readJson(env, WATCHDOG_KEY)) || {};
  const save = (value) => env.INBOX.put(keyOf(env, WATCHDOG_KEY), JSON.stringify(value), { httpMetadata: { contentType: "application/json" } });
  const age = index && index.generated_at ? (Date.now() - Date.parse(index.generated_at)) / 36e5 : Infinity;
  if (age <= STALE_HOURS) {
    if (memo.alerted) {
      await tell(env, "✅ The scraper is running again.");
      await save({});
    }
    return "fresh";
  }
  const kicks = memo.kicks || 0;
  if (memo.last_kick && Date.now() - Date.parse(memo.last_kick) < KICK_EVERY_HOURS * 36e5) return "waiting";
  if (kicks >= MAX_KICKS) return "gave up";  // said so already; a human has to look at GitHub Actions
  await github(env, "PUT", "scraper.yml/enable");
  const started = await github(env, "POST", "scraper.yml/dispatches", { ref: "main" });
  const silent = Number.isFinite(age) ? `${Math.round(age)} hours` : "a long time";
  if (!memo.alerted) {
    await tell(env, started.ok
      ? `🛟 No scraper run for ${silent}. I re-enabled the schedule and started a run myself.`
      : `⚠️ No scraper run for ${silent}, and I could not start one (GitHub answered ${started.status}). Check the Actions tab and the Worker's GITHUB_TOKEN.`);
  } else if (kicks + 1 >= MAX_KICKS) {
    await tell(env, `⚠️ Still no scraper run after ${MAX_KICKS} attempts. Please look at the repository's Actions tab.`);
  }
  await save({ alerted: true, last_kick: new Date().toISOString(), kicks: kicks + 1 });
  return started.ok ? "kicked" : "kick failed";
}

// ---------------------------------------------------------------------------- application replies by email (optional)
// Cloudflare Email Routing can hand mail for an address on your domain (say jobs@yourdomain) to this Worker. Apply
// with that address, and the answers come back through here: the mail is matched to one of your applications by the
// sender's domain or the company name, classified from the subject and the text, recorded as an outcome when the
// reading is clear, and summarised in Telegram (subject and verdict only, never the body). It is a reading, not a
// judgement: /outcome code <status> overrides it.
const MAIL_KINDS = [
  ["rejected", /\b(unfortunately|regret|regrettably|not (?:been )?(?:selected|successful|shortlisted|retained)|decided (?:not )?to (?:move|go|proceed) (?:forward|ahead) with other|not (?:be )?moving forward|other candidates|will not be (?:progressing|proceeding)|position has been filled|malheureusement|ne (?:pouvons|pourrons) pas donner suite|ne donnerons pas suite|n'?(?:a|avons) pas (?:été )?retenu|candidature n'?a pas été retenue|pas été sélectionn)/i],
  ["offer", /\b(offer letter|pleased to (?:offer|extend)|job offer|formal offer|proposition d'?embauche|offre d'?emploi ferme|heureux de vous proposer)\b/i],
  ["interview", /\b(interview|entretien|phone screen|screening call|video call|schedule a (?:call|conversation|chat|meeting)|availability (?:for|to)|book a time|invite you to|would like to (?:meet|speak|talk)|next (?:step|stage|round)|technical (?:test|assessment|exercise)|take-?home)\b/i],
  ["received", /\b(thank you for (?:applying|your application|your interest)|application (?:has been )?received|we have received your application|accusé de réception|nous avons bien reçu|merci (?:pour|de) votre candidature)\b/i],
];
// An acknowledgement often mentions a possible interview ("if selected for an interview, we will contact you"): only a
// mail that actually arranges something counts as an invitation when it also reads like an acknowledgement.
const MAIL_ARRANGES = /\b(schedule a (?:call|conversation|chat|meeting)|availability (?:for|to)|book a time|invite you to|would like to (?:meet|speak|talk)|vos disponibilit|convenir d'?un)/i;
// What a mail may change by itself. An offer or a rejection already recorded is never overwritten by a later mail
// (a newsletter from the same employer reads like anything): it is reported, and /outcome decides.
const MAIL_MAY_FOLLOW = { applied: ["interview", "rejected", "offer"], ghosted: ["interview", "rejected", "offer"], interview: ["interview", "rejected", "offer"] };
const MAIL_MAX_BYTES = 1024 * 1024;
const MAIL_STATUS_TEXT = { rejected: "a rejection", offer: "an offer", interview: "an interview invitation", received: "an acknowledgement" };

function decodeMailPart(body, encoding) {
  const enc = String(encoding || "").toLowerCase();
  if (enc.includes("base64")) {
    try { return new TextDecoder().decode(Uint8Array.from(atob(body.replace(/\s+/g, "")), (c) => c.charCodeAt(0))); } catch { return body; }
  }
  if (enc.includes("quoted-printable")) {
    const bytes = body.replace(/=\r?\n/g, "").replace(/=([0-9A-Fa-f]{2})/g, (_, h) => String.fromCharCode(parseInt(h, 16)));
    try { return new TextDecoder().decode(Uint8Array.from(bytes, (c) => c.charCodeAt(0))); } catch { return bytes; }
  }
  return body;
}

/** Best-effort text of an email from its raw MIME: the first text/plain part, else the stripped text/html one. */
function mailText(raw, depth = 0) {
  const head = raw.slice(0, raw.search(/\r?\n\r?\n/) + 1);
  const boundary = (head.match(/boundary="?([^";\r\n]+)"?/i) || [])[1];
  const parts = boundary ? raw.split(new RegExp(`--${boundary.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?:--)?`)) : [raw];
  const pick = (type) => {
    for (const part of parts) {
      const split = part.search(/\r?\n\r?\n/);
      if (split === -1) continue;
      const headers = part.slice(0, split);
      if (depth < 3 && part !== raw && /content-type:\s*multipart\//i.test(headers)) {  // multipart/alternative inside multipart/mixed
        const nested = mailText(part.replace(/^\s+/, ""), depth + 1);
        if (nested) return nested;
        continue;
      }
      if (!new RegExp(`content-type:\\s*${type}`, "i").test(headers)) continue;
      const encoding = (headers.match(/content-transfer-encoding:\s*([^\r\n]+)/i) || [])[1];
      return decodeMailPart(part.slice(split).trim(), encoding);
    }
    return "";
  };
  const plain = pick("text/plain");
  if (plain) return plain;
  const page = pick("text/html");
  return page ? page.replace(/<style[\s\S]*?<\/style>/gi, " ").replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/\s+/g, " ") : "";
}

function classifyMail(subject, text) {
  const haystack = `${subject}\n${text.slice(0, 4000)}`;
  for (const [kind, pattern] of MAIL_KINDS) {
    if (!pattern.test(haystack)) continue;
    if (kind === "interview" && MAIL_KINDS[3][1].test(haystack) && !MAIL_ARRANGES.test(haystack)) return "received";
    return kind;
  }
  return "";
}

function matchApplication(prefs, fromAddress, subject, text) {
  const domain = (String(fromAddress).split("@")[1] || "").toLowerCase().replace(/^(mail|jobs|careers|recruiting|hr|noreply|no-reply|talent)\./, "");
  const domainCore = domain.split(".")[0];
  const haystack = fold(`${subject} ${text.slice(0, 3000)}`);
  let best = null;
  for (const [id, a] of Object.entries(prefs.applied)) {
    const company = fold(a.company || "");
    const words = company.split(/[^a-z0-9]+/).filter((w) => w.length >= 3 && !["the", "inc", "ltd", "llc", "gmbh", "corp", "group", "company"].includes(w));
    let score = 0;
    if (words.length && domainCore.length >= 3 && words.some((w) => domainCore.includes(w) || w.includes(domainCore))) score += 3;
    if (company && haystack.includes(company)) score += 2;
    if (a.title && haystack.includes(fold(a.title))) score += 2;
    if (score > (best ? best.score : 1)) best = { id, score, application: a };
  }
  return best;
}

async function handleMail(message, env) {
  if (!env.INBOX) return;
  const subject = message.headers.get("subject") || "(no subject)";
  // a huge mail (attachments) is judged by its subject alone; bulk mail is not an answer to an application
  const raw = Number(message.rawSize || 0) > MAIL_MAX_BYTES ? "" : await new Response(message.raw).text();
  const bulk = /^(bulk|list|junk)$/i.test(message.headers.get("precedence") || "") || Boolean(message.headers.get("list-unsubscribe"));
  const text = raw ? mailText(raw) : "";
  const kind = classifyMail(subject, text);
  let prefs = await loadPrefs(env);
  let found = matchApplication(prefs, message.from, subject, text);
  const sender = String(message.from).replace(/^[^<]*<|>.*$/g, "");
  const about = found ? `<b>${esc(found.application.title)}</b>${found.application.company ? ` — ${esc(found.application.company)}` : ""}` : "";
  // recorded only when the mail is clearly about that application (the sender's domain, or the employer and the title
  // together), is not bulk mail, and the step follows from the status on record
  const follows = found && (MAIL_MAY_FOLLOW[found.application.status || "applied"] || []).includes(kind);
  if (found && found.score >= 3 && !bulk && follows) {
    const now = new Date().toISOString();
    for (let attempt = 0; attempt < 2 && found; attempt += 1) {
      const info = found.application;
      info.status = kind;
      (info.history = info.history || []).push({ at: now, status: kind, source: "email" });
      if (await savePrefs(env, prefs)) break;
      prefs = await loadPrefs(env);  // changed meanwhile: once more on the fresh copy
      found = matchApplication(prefs, message.from, subject, text);
    }
    if (!found) return;
    const code = await codeOf(found.id);
    await reply(env, [`📧 Mail from ${esc(sender)} reads like ${MAIL_STATUS_TEXT[kind]} for ${about} — recorded as <b>${kind}</b>.\n`
      + `<i>${esc(subject.slice(0, 120))}</i>\nWrong? /outcome ${code} applied${kind === "interview" ? " · /prep " + code + " for the interview sheet" : ""}`]);
    if (kind === "interview") await dispatchToPython(env, { update_id: Date.now() }, `/prep ${code}`, "", "🎤 Preparing your interview sheet, about a minute.");
    return;
  }
  if (bulk && !found) return;  // a newsletter about nobody we applied to: not worth a message
  const verdict = kind ? `reads like ${MAIL_STATUS_TEXT[kind]}` : "is not one I can classify";
  const held = found && kind && kind !== "received"
    ? `\nNot recorded (${bulk ? "bulk mail" : found.score < 3 ? "the link to that application is weak" : `it is already '${found.application.status || "applied"}'`}): /outcome ${await codeOf(found.id)} ${kind} records it.`
    : "";
  await reply(env, [`📧 Mail from ${esc(sender)} ${verdict}${found ? ` (probably about ${about})` : ""}.\n<i>${esc(subject.slice(0, 120))}</i>${held}`
    + (found && kind ? "" : "\nTell me what it was: /outcome code interview|rejected|offer")]);
}

export default {
  async email(message, env, ctx) {
    ctx.waitUntil(handleMail(message, env).catch((error) =>
      tell(env, `⚠️ Could not read an email: ${String(error && error.message ? error.message : error).slice(0, 200)}`)));
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(watchdog(env).catch((error) => console.log("watchdog error", String(error && error.message ? error.message : error))));
  },

  async fetch(request, env, ctx) {
    if (request.method === "GET") {
      const page = await dashboard(request, env);
      if (page) return page;
    }
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
    if (update && !(update.message || update.channel_post || update.business_message)) update.edited = Boolean(message);
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
      ];
      // how the Worker is set up is told only to the configured chat or its owner, never to a stranger asking /id
      const asker = message.from && !message.from.is_bot ? String(message.from.id) : "";
      if (configured || asker === String(env.TELEGRAM_OWNER_ID || env.TELEGRAM_CHAT_ID)) {
        lines.push(`instant replies: ${env.INBOX ? "on (R2 bound)" : "off (add the INBOX R2 binding)"} · AI: ${env.AI ? "on" : "off"}`
          + ` · dashboard: ${String(env.DASHBOARD_KEY || "").length >= 16 ? "on" : "off"}`);
      }
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
