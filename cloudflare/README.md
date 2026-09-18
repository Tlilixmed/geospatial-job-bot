# Instant Telegram replies with a Cloudflare Worker

Without this, the bot answers when something polls Telegram: hourly, plus at each scraper run. With it,
Telegram pushes every message to a Worker, which starts the `Telegram commands` workflow with your text.
You get "On it" immediately and the real reply in about a minute. Cost: free (Workers free plan), and
GitHub minutes are spent only when you actually send a message.

## 1. GitHub token for the Worker
GitHub → your avatar → **Settings → Developer settings → Personal access tokens → Fine-grained tokens →
Generate new token**
- Repository access: **Only select repositories** → `geospatial-job-bot`
- Permissions → Repository → **Actions: Read and write** (nothing else)
- Generate and copy the token (`github_pat_…`).

## 2. Create the Worker
Cloudflare dashboard → **Workers & Pages → Create → Worker** → name it `geojobbot-telegram` → Deploy →
**Edit code** → replace everything with the contents of `cloudflare/worker.js` → **Deploy**.
Copy the Worker URL (`https://geojobbot-telegram.<you>.workers.dev`).

## 3. Worker secrets
Worker → **Settings → Variables and secrets → Add**, each with type **Secret**:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | your BotFather token |
| `TELEGRAM_CHAT_ID` | your chat id (same as the GitHub secret) |
| `WEBHOOK_SECRET` | a random string, letters and digits only, e.g. 32 characters |
| `GITHUB_TOKEN` | the fine-grained token from step 1 |
| `GITHUB_REPO` | `Tlilixmed/geospatial-job-bot` |

## 4. Point Telegram at the Worker
Open this in a browser (fill in the three values):

```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=<WORKER_URL>&secret_token=<WEBHOOK_SECRET>&allowed_updates=["message"]
```

It should answer `{"ok":true,"result":true,"description":"Webhook was set"}`.

## 5. Stop the hourly polling
GitHub repository → **Settings → Secrets and variables → Actions → Variables → New variable**:
`TELEGRAM_WEBHOOK` = `true`. Scheduled polls are then skipped (skipped jobs bill nothing).

## Test
Send `/status` to the bot. You should see "On it" at once and the status about a minute later.
If "On it" never arrives, check the Worker's **Logs**; if it arrives with a GitHub error, the token in
step 1 lacks *Actions: Read and write* or `GITHUB_REPO` is misspelled.

## Undo
`https://api.telegram.org/bot<TOKEN>/deleteWebhook`, then delete the `TELEGRAM_WEBHOOK` variable:
polling resumes.
