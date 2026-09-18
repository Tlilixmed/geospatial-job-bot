/**
 * Telegram webhook -> GitHub Actions bridge for the geospatial job bot.
 *
 * Telegram calls this Worker the instant you send the bot a message. The Worker checks the webhook
 * secret and that the message comes from YOUR chat, tells you it is on it, and dispatches
 * .github/workflows/commands.yml with the message text. The workflow executes the command against
 * R2 and replies, typically within a minute. Nothing runs (and nothing is billed) while you are silent.
 *
 * Secrets to set on the Worker (Settings -> Variables and secrets, type "Secret"):
 *   TELEGRAM_BOT_TOKEN   the bot token from BotFather
 *   TELEGRAM_CHAT_ID     your chat id (same value as the GitHub secret)
 *   WEBHOOK_SECRET       any random string of letters and digits; also given to Telegram in setWebhook
 *   GITHUB_TOKEN         fine-grained personal access token: this repository only, Actions = Read and write
 *   GITHUB_REPO          e.g. Tlilixmed/geospatial-job-bot
 */
export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("geospatial job bot webhook");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    let update;
    try { update = await request.json(); } catch { return new Response("bad request", { status: 400 }); }
    const message = update && update.message;
    const text = message && typeof message.text === "string" ? message.text.trim() : "";
    // Always answer 200 to Telegram from here on, otherwise it retries the same update for hours.
    if (!text || String(message.chat && message.chat.id) !== String(env.TELEGRAM_CHAT_ID)) {
      return new Response("ignored");
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
        body: JSON.stringify({ ref: "main", inputs: { text: text.slice(0, 500) } }),
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
