# Instant Telegram replies with a Cloudflare Worker

Without this, the bot answers when something polls Telegram: hourly, plus at each scraper run. With it,
Telegram pushes every message to a Worker. Cost: free (Workers free plan).

**Two speeds.** With the `INBOX` binding of step 6 the Worker answers most messages itself, in under a second,
from two files in the bucket: `state/index.json` (published by every scraper run: current matches, codes, scores,
AI notes, sponsor hits, run status, help text) and `state/prefs.json` (your preferences and applications).

| Answered instantly by the Worker | Handed to the `Telegram commands` workflow ("On it", about a minute) |
|---|---|
| `/jobs` `/high` `/range` `/search` `/why` `/ai` `/sponsors` `/visa` `/sources` `/prospects` `/status` `/signals` `/muted` `/help` | `/run` `/pitch` `/prep` `/approach` `/watch` `/unwatch` `/weekly` `/radar` `/skills` `/learning` |
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
https://api.telegram.org/bot<TOKEN>/setWebhook?url=<WORKER_URL>&secret_token=<WEBHOOK_SECRET>&allowed_updates=["message","callback_query"]
```

It should answer `{"ok":true,"result":true,"description":"Webhook was set"}`. `callback_query` is what a tap on a
digest button sends; add `"channel_post"` to the list if the bot lives in a channel.

**Already set up before the buttons existed?** Check once what Telegram delivers:

```
https://api.telegram.org/bot<TOKEN>/getWebhookInfo
```

If `allowed_updates` is listed and lacks `"callback_query"` (the earlier version of this step registered only
`["message"]`), open the `setWebhook` link above again with the same URL and secret: taps are otherwise never delivered
and the buttons just spin. A webhook registered without `allowed_updates` already receives them. Leaving the parameter
out of a new `setWebhook` call keeps the previous list, so always spell it out.

## 5. Stop the hourly polling
GitHub repository → **Settings → Secrets and variables → Actions → Variables → New variable**:
`TELEGRAM_WEBHOOK` = `true`. Scheduled polls are then skipped (skipped jobs bill nothing), and the scraper's digests
carry one-tap buttons: a number per job, and under the number you tap ✅ Applied, 🙈 Hide, ❓ Why (with ↩️ to undo).
Only the Worker can answer a tap, which is why the buttons wait for this variable. Only the owner's taps count: in a
group, set `TELEGRAM_OWNER_ID` (see *Which chat?* below), or every tap is refused.
Without the bucket of step 6 a tap still works, the slow way: it starts the commands workflow like the typed command
(reply in about a minute), and ↩️ Undo needs the bucket.

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

## 8. Optional: private dashboard
Worker → **Settings → Variables and secrets → Add** → type **Secret** → name `DASHBOARD_KEY`, value: a random string of
at least 16 letters and digits (treat it like a password) → **Deploy**. Then open

```
https://geojobbot-telegram.<you>.workers.dev/dash/<DASHBOARD_KEY>
```

Matches (filter, sort, visa and sponsor chips), applications by status, visa routes, employers, sources. The page is
read-only, is never cached or indexed, and needs the `INBOX` binding. Anyone with the link can read it: do not share it,
and change the secret to revoke it. Without the secret the route answers 404.

## 9. Strongly recommended: the run filler and watchdog (a Cron Trigger)
GitHub's scheduler is best-effort, and for this repository it is far from the two-hourly schedule: between
2026-09-19 and 2026-10-10 it **dropped 61% of the scraper's slots** (155 of 255), with a median gap of 5.1 hours and
gaps of up to 8.9 hours. The `Telegram commands` schedule at :17 is dropped just as often, so moving the cron
minute does not help. Scheduled workflows are also **disabled silently after 60 days without repository activity**.

Worker → **Settings → Triggers → Cron Triggers → Add** → `41 * * * *` (every hour, at minute 41) → Save.
If a trigger `0 * * * *` exists from an earlier version, replace it.

Every hour the Worker asks R2 when `state/index.json` was last uploaded (a `head()`, the file is not read). When that
is at least 1¾ hours ago and GitHub has no scraper run queued or in progress, it re-enables the workflow and starts
a run itself, without a message: that is routine gap filling, and the GitHub schedule stays as a backup. A scraper
that crashes before writing the index is started again after 1, 2 and 4 hours, then left alone until a run gets
through (a push, the schedule or **Run workflow**). After 6 hours without a run the Worker **rings** once
(`🛟 No scraper run for 7 hours…` or `⚠️ …` with the reason), once more when it gives up, and reports
`✅ The scraper is running again` quietly afterwards. A start GitHub refuses (an expired token, or one without
*Actions: Read and write*) rings at once with the HTTP status, is named again in the 6-hour alert, and is tried again
every hour without using up the four starts, so the filler resumes by itself once the token is replaced.

It uses the `GITHUB_TOKEN` (*Actions: Read and write* covers listing runs) and the `INBOX` binding you already have.
Cost: 24 invocations a day, each one R2 `head()` and at most four GitHub requests; Workers Free allows 5 Cron
Triggers. To see the effect, compare `event` = `schedule` with `workflow_dispatch` in the scraper's run list.

## 10. Optional: LinkedIn job alerts and employer replies by email
You need a domain, and its DNS must be on Cloudflare (the free plan is enough). You do **not** need a mailbox or paid
email: **Email Routing** is free and hands the mail straight to this Worker, which keeps nothing but the job lines.

**a. Put the domain on Cloudflare** (skip if it is already there). Cloudflare dashboard → **Add a domain** → Free plan → check
that the imported DNS records look complete → change the nameservers at your registrar to the two Cloudflare gives you. It
can take up to 24 hours. If the domain already receives mail somewhere, stop here: step b replaces its MX records.

**b. Turn on Email Routing.** **Compute → Email Service → Email Routing → Onboard Domain**, pick the domain, accept the DNS
records (on older dashboards: your domain → **Email → Email Routing**). You can skip "destination addresses": they are only
for forwarding to a mailbox.

**c. Create the address.** **Routing Rules → Create routing rule**. Email pattern: something hard to guess, such as
`alerts-k7m2q`. Action: **Send to a Worker**, Worker: `geojobbot-telegram`. Do not use a catch-all, and do not put the address
in this public repository.

**d. Forward the alerts to it.** LinkedIn sends alerts to your account's main address, so forward them from that inbox. In Gmail:
1. **Settings → Forwarding and POP/IMAP → Add a forwarding address**, enter the address. The confirmation code arrives in
   Telegram, because this Worker is that address's only reader. Enter it in Gmail and choose **Verify**. There is no need to
   forward all your mail.
2. Search box → **Show search options**. From: `jobalerts-noreply@linkedin.com OR jobs-listings@linkedin.com` → **Create filter** →
   **Forward it to** the address → **Create filter**.

**e. Create the alerts on LinkedIn.** **Jobs**, search a title and a place, **Set alert**, daily, by email. Titles in both
languages and the countries you target, for example `GIS` and `Géomaticien` in France, `GIS Analyst` in Canada.

**f. Check it.** The first alert brings *📬 LinkedIn alert received, N job links*. After the next scraper run, `/sources` lists
`linkedin_alerts`. Two messages mean something needs a look: *failed the sender check* (the mail did not prove it came from
LinkedIn) and *no job link I recognise* (a mail with no jobs, which is normal for "you created an alert"; if real alerts do
it, their format changed). `/sources` showing `linkedin_alerts` as PARTIAL also points to a format change.

What the bot does with an alert: the Worker accepts it only when Cloudflare reports `dmarc=pass` for LinkedIn's domain, removes
every link's parameters (LinkedIn's carry one-time sign-in tokens) and the footer that names you, and leaves the rest in the
bucket for a week (`mail-alerts/`). Each scraper run reads the jobs and fetches up to 12 posting texts through JobSpy's LinkedIn
reader, so the normal rules apply. A posting whose text cannot be read stays title-only evidence, at most *Possible*.

**Employer replies** can use the same address, or a second rule such as `jobs@yourdomain`: use it on your applications, or forward
employer mail to it.

Each mail is matched to one of your applications by the sender's domain or the company name, read for its meaning
(rejection, interview invitation, offer, acknowledgement; English and French wording) and, when the reading is clear
and the application is known, recorded as an outcome, with a Telegram line such as *📧 Mail from talent@acme.com reads
like an interview invitation for GIS Analyst — Acme — recorded as interview* (subject and verdict only; the body never
reaches the chat). An interview invitation also triggers the interview sheet. `/outcome code applied` undoes a wrong
reading. Mail that matches nothing is only announced.

## Which chat? Getting TELEGRAM_CHAT_ID right
Send `/id` in the chat you want to use. The bot answers there with the chat id, the chat type, your user id, and
whether that chat is the configured one. Then:

- Put the **chat id** in `TELEGRAM_CHAT_ID` in **both** places: the GitHub secret (alerts and replies are sent
  there) and the Worker secret (messages from there are obeyed). They must be identical.
- A private chat with the bot is the simplest choice: its id is also your user id, so you are obeyed everywhere.
- If you choose a group or channel (ids start with `-100`), also add the Worker secret `TELEGRAM_OWNER_ID` with
  **your user id**, so you are still obeyed when you write to the bot privately. Digest buttons need it too: a tap
  changes your applications, so only the owner's taps count, and without `TELEGRAM_OWNER_ID` the owner is the group
  itself, which never taps; every tap is then refused (the button stops spinning, nothing changes).
- Replies always go to `TELEGRAM_CHAT_ID`, wherever you wrote from.

## If the bot is in a group or a channel rather than a private chat
- **Group:** bots only receive messages starting with `/` while *privacy mode* is on, so plain sentences never reach
  the Worker. In BotFather: `/setprivacy` → choose the bot → **Disable**, then remove the bot from the group and add
  it again (the setting applies on joining).
- **Channel:** the bot must be an administrator; posts arrive as `channel_post`, which the Worker accepts.
- The `setWebhook` URL should list the update types you need, e.g. `allowed_updates=["message","callback_query","channel_post"]`.
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
