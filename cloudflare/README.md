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

## 6. Private hand-over through R2 (do this if the repository is public)
On a public repository anyone can read a workflow run's inputs, so the text of your messages would be visible in
the Actions tab. Bind the bot's bucket to the Worker and messages travel through your private bucket instead.

Worker → **Settings → Bindings → Add → R2 bucket**
- Variable name: `INBOX`
- R2 bucket: the bot's bucket (the value of your `R2_BUCKET_NAME` secret)
- Save, then **Deploy**.

Nothing else changes: the Worker writes `inbox/<id>.json`, starts the workflow with `inbox=true`, and the workflow
reads and deletes the file.

## 7. Optional: AI understanding of free text (free tier)
Worker → **Settings → Bindings → Add → Workers AI** → Variable name: `AI` → Save → **Deploy**.

Workers AI includes a free daily allowance that is far more than a personal bot uses. The bot's own rules still
decide first; the AI reading is used only when the rules see nothing but a search, it must be one of the known
commands (and refer to a job code that exists), and the reply marks it: `↪ /resume · AI`.

After changing `cloudflare/worker.js` in the repository, paste the new version into the Worker (**Edit code → Deploy**).

## If the bot is in a group or a channel rather than a private chat
- **Group:** bots only receive messages starting with `/` while *privacy mode* is on, so plain sentences never reach
  the Worker. In BotFather: `/setprivacy` → choose the bot → **Disable**, then remove the bot from the group and add
  it again (the setting applies on joining).
- **Channel:** the bot must be an administrator; posts arrive as `channel_post`, which the Worker accepts.
- The `setWebhook` URL should list the update types you need, e.g. `allowed_updates=["message","channel_post"]`.
- The Worker logs `ignored update {...}` with the reason (Observability tab) whenever it drops something.

## Test
Send `/status` to the bot. You should see "On it" at once and the status about a minute later.
If "On it" never arrives, check the Worker's **Logs**; if it arrives with a GitHub error, the token in
step 1 lacks *Actions: Read and write* or `GITHUB_REPO` is misspelled.

## Undo
`https://api.telegram.org/bot<TOKEN>/deleteWebhook`, then delete the `TELEGRAM_WEBHOOK` variable:
polling resumes.
