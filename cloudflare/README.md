# Instant Telegram replies with a Cloudflare Worker

Without this, the bot answers when something polls Telegram: hourly, plus at each scraper run. With it,
Telegram pushes every message to a Worker. Cost: free (Workers free plan).

**Two speeds.** With the `INBOX` binding of step 6 the Worker answers most messages itself, in under a second,
from two files in the bucket: `state/index.json` (published by every scraper run: current matches, codes, scores,
AI notes, sponsor hits, run status, help text) and `state/prefs.json` (your preferences and applications).

| Answered instantly by the Worker | Handed to the `Telegram commands` workflow ("On it", about a minute) |
|---|---|
| `/jobs` `/high` `/range` `/search` `/why` `/ai` `/sponsors` `/status` `/signals` `/muted` `/help` | `/run` `/pitch` `/weekly` `/radar` `/skills` `/learning` |
| `/applied` `/outcome` `/hide` `/unhide` `/mute` `/unmute` `/threshold` `/locations` `/interns` `/possible` `/pause` `/resume` | free-text sentences that would change a setting or record an application |
| plain words: `status`, `help`, `top 10`, `high 5`, `ai`, `sponsors`, a bare job code | anything when the index is missing (before the first run with this version) |
| free text the Worker's AI reads as a **read-only** view (marked `↪ /sponsors · AI`) | |

The Python rules stay the single authority for free text that changes something: the Worker's AI alone can only
*show* things. `/id` reports whether instant replies are on.

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

## 6. Bind the bucket: instant replies and a private hand-over
This one binding does two things. It lets the Worker read `state/index.json` and read/write `state/prefs.json`, which
is what makes replies instant. And on a public repository, where anyone can read a workflow run's inputs, it keeps
the text of your messages out of the Actions tab: they travel through your private bucket instead.

Worker → **Settings → Bindings → Add → R2 bucket**
- Variable name: `INBOX`
- R2 bucket: the bot's bucket (the value of your `R2_BUCKET_NAME` secret)
- Save, then **Deploy**.

For messages it hands over, the Worker writes `inbox/<id>.json`, starts the workflow with `inbox=true`, and the
workflow reads and deletes the file.

## 7. Optional: AI understanding of free text (free tier)
Worker → **Settings → Bindings → Add → Workers AI** → Variable name: `AI` → Save → **Deploy**.

Workers AI includes a free daily allowance that is far more than a personal bot uses. Its reading must be one of
the known commands. When that command is a read-only view the Worker answers at once (`↪ /sponsors · AI`);
otherwise the reading travels with the message as a hint, and the Python rules decide first: the hint is used only
when the rules see nothing but a search, and must refer to a job code that exists (`↪ /resume · AI`).

After changing `cloudflare/worker.js` in the repository, paste the new version into the Worker (**Edit code → Deploy**).

## Which chat? Getting TELEGRAM_CHAT_ID right
Send `/id` in the chat you want to use. The bot answers there with the chat id, the chat type, your user id, and
whether that chat is the configured one. Then:

- Put the **chat id** in `TELEGRAM_CHAT_ID` in **both** places: the GitHub secret (alerts and replies are sent
  there) and the Worker secret (messages from there are obeyed). They must be identical.
- A private chat with the bot is the simplest choice: its id is also your user id, so you are obeyed everywhere.
- If you choose a group or channel (ids start with `-100`), also add the Worker secret `TELEGRAM_OWNER_ID` with
  **your user id**, so you are still obeyed when you write to the bot privately.
- Replies always go to `TELEGRAM_CHAT_ID`, wherever you wrote from.

## If the bot is in a group or a channel rather than a private chat
- **Group:** bots only receive messages starting with `/` while *privacy mode* is on, so plain sentences never reach
  the Worker. In BotFather: `/setprivacy` → choose the bot → **Disable**, then remove the bot from the group and add
  it again (the setting applies on joining).
- **Channel:** the bot must be an administrator; posts arrive as `channel_post`, which the Worker accepts.
- The `setWebhook` URL should list the update types you need, e.g. `allowed_updates=["message","channel_post"]`.
- The Worker logs `ignored update {...}` with the reason (Observability tab) whenever it drops something.

## Test
Send `/id`: the last line says whether instant replies and the AI are on. Send `/status`: with the `INBOX` binding
and at least one scraper run since this version, the status arrives within a second and ends with *answered
instantly from the index of …*. Send `/weekly`: you should see "On it" at once and the summary about a minute later.
If nothing arrives, check the Worker's **Logs**; if "On it" arrives with a GitHub error, the token in
step 1 lacks *Actions: Read and write* or `GITHUB_REPO` is misspelled.

The Worker has its own tests: `node --test tests/worker/worker.test.mjs` (also run by the Tests workflow).

## Undo
`https://api.telegram.org/bot<TOKEN>/deleteWebhook`, then delete the `TELEGRAM_WEBHOOK` variable:
polling resumes.
