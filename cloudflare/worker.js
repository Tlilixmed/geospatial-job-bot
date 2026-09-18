/**
 * Telegram webhook -> GitHub Actions bridge for the geospatial job bot.
 *
 * Telegram calls this Worker the instant you send the bot a message. The Worker checks the webhook
 * secret and that the message comes from YOUR chat, tells you it is on it, and starts
 * .github/workflows/commands.yml, which executes the message against R2 and replies within a minute.
 *
 * Secrets (Settings -> Variables and secrets, type "Secret"):
 *   TELEGRAM_BOT_TOKEN   the bot token from BotFather
 *   TELEGRAM_CHAT_ID     your chat id (same value as the GitHub secret)
 *   WEBHOOK_SECRET       any random string of letters and digits; also given to Telegram in setWebhook
 *   GITHUB_TOKEN         fine-grained personal access token: this repository only, Actions = Read and write
 *   GITHUB_REPO          e.g. Tlilixmed/geospatial-job-bot
 *
 * Optional bindings (Settings -> Bindings -> Add):
 *   INBOX  R2 bucket = the bot's bucket. Messages are then handed over through R2 (inbox/<id>.json) instead of
 *          workflow inputs. REQUIRED for privacy on a public repository, where workflow inputs are world-readable.
 *   AI     Workers AI. Free-text messages get an AI reading as a second opinion; the bot's own rules decide first
 *          and the AI answer is only accepted if it is one of the known commands.
 */
const COMMANDS = ["jobs", "high", "range", "search", "why", "applied", "hide", "unhide", "mute", "unmute", "muted",
  "threshold", "locations", "interns", "pause", "resume", "status", "weekly", "run", "help", "pitch"];
const HINT_RE = new RegExp(`^/(${COMMANDS.join("|")})(\\s[^\\n]{0,100})?$`);

const SYSTEM_PROMPT = `You translate one chat message (English, French or Arabic, typos possible) sent to a job-alert bot
into exactly ONE bot command. Answer with the command line only, no explanation, no quotes.
Commands:
/jobs [n]            best current job matches (n = how many)
/high [n]            only the strongest matches
/range LOW HIGH      jobs whose score (0-100) is between LOW and HIGH ("above 80" -> /range 80 100)
/search WORDS        look for jobs about a skill, title, company or place (keep only the meaningful words)
/why CODE            explain one job; CODE is a 5-character tag like a3f9c
/pitch CODE          write a cover letter / application note for that job
/applied CODE        the user applied to that job        /applied   list applications
/hide CODE           the user is not interested          /unhide CODE   bring it back
/mute TEXT           stop showing a company or title word /unmute TEXT   /muted  list mutes
/threshold HIGH [POSSIBLE]   change score cut-offs
/locations A, B      preferred countries                 /locations reset
/interns on|off      include or exclude internships
/pause  /resume      stop or restart alerts
/status              is the bot healthy, last run, settings
/weekly              summary of applications and open matches
/run                 search for new jobs right now
/help                what the bot can do
If the message is a question about jobs of some kind, use /search. If nothing fits, answer /help.`;

async function aiHint(env, text) {
  const result = await env.AI.run("@cf/meta/llama-3.1-8b-instruct", {
    messages: [{ role: "system", content: SYSTEM_PROMPT }, { role: "user", content: text.slice(0, 300) }],
    max_tokens: 40,
    temperature: 0,
  });
  const line = String((result && result.response) || "").trim().split("\n")[0].trim().replace(/^["'`]|["'`.]$/g, "");
  return HINT_RE.test(line) ? line : "";
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("geospatial job bot webhook");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    let update;
    try { update = await request.json(); } catch { return new Response("bad request", { status: 400 }); }
    const message = update && update.message;
    const text = message && typeof message.text === "string" ? message.text.trim().slice(0, 500) : "";
    // Always answer 200 to Telegram from here on, otherwise it retries the same update for hours.
    if (!text || String(message.chat && message.chat.id) !== String(env.TELEGRAM_CHAT_ID)) {
      return new Response("ignored");
    }

    let hint = "";
    if (!text.startsWith("/") && env.AI) {
      try { hint = await aiHint(env, text); } catch { hint = ""; }
    }

    let inputs;
    if (env.INBOX) {  // private hand-over: nothing about the message appears in the public workflow run
      const key = `inbox/${String(update.update_id).padStart(12, "0")}.json`;
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
    const note = dispatch.ok
      ? "⏳ On it. Reply in about a minute."
      : `⚠️ I could not start the command (GitHub answered ${dispatch.status}). Check the Worker's GITHUB_TOKEN.`;
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text: note, disable_notification: true }),
    });
    return new Response("ok");
  },
};
