# Geospatial Job Discovery Bot

Finds relevant GIS / geospatial / surveying / LiDAR / remote-sensing jobs from legitimate public sources, scores them deterministically, merges duplicates across sources, and sends Telegram alerts. Cloudflare R2 is the only persistent state, so any fresh GitHub Actions runner continues exactly where the last one stopped.

---

## 1. How it works

```
             ┌───────────── discovery phase (parallel) ─────────────┐
             │ career pages + sitemaps │ SearXNG │ DuckDuckGo │ Common Crawl │
             └──────┬───────────────────────┬──────────────────────────┘
          ATS boards registered      job-page URLs queued
                    ▼                        ▼
             ┌──────────── extraction phase (parallel) ─────────────┐
             │ 8 ATS APIs │ generic page extractor │ feeds │ USAJOBS │ JobSpy │
             └──────────────────────────┬───────────────────────────┘
                                        ▼
         fusion (one canonical job per posting) → deterministic scoring
                                        ▼
        state merge → checkpoint save to R2 → Telegram alerts → final save
                                        ▼
               run report + diagnostics + raw snapshots in R2
```

### Discovery layers

| Layer | Backend | What it does |
|---|---|---|
| Public ATS APIs | `greenhouse`, `lever`, `ashby`, `smartrecruiters`, `workable`, `recruitee`, `personio`, `workday` | Official public job-board endpoints. Every board gets a validation status. |
| Board discovery | `commoncrawl`, search backends, career pages, generic pages | Discovers *new* company boards and adds them to a persistent registry. |
| Career pages + sitemaps | `career_sites` | Configured company pages: JSON-LD jobs, embedded ATS boards, job links, job sitemaps. |
| Generic extraction | `generic_pages` | Any queued public job page: JSON-LD `JobPosting` → embedded JSON → structured HTML. ATS URLs are fetched through the ATS API instead. |
| Public feeds | `remotive`, `jobicy`, `himalayas`, `arbeitnow`, `remoteok`, `rss_feeds`, `usajobs` | Rate-respecting JSON/RSS feeds (each has a minimum interval). `rss_feeds` ships with GoGeomatics (Canada), GISjobs.com, Government of Canada Job Bank searches and Tunisie Travail searches. |
| Job boards without feeds | `career_sites` with `source_type = "feed"` | Keyword search pages of boards such as Keejob (Tunisia): job links are followed and each posting's JSON-LD is read. |
| Aggregator APIs (optional) | `adzuna`, `jooble`, `jsearch` | Free API keys. Adzuna covers Canada, UK, US and more; Jooble covers Tunisia, the Maghreb and Canada; JSearch returns Google for Jobs results (LinkedIn, Indeed, Glassdoor, employer sites) with full descriptions. |
| Search (optional) | `search_searxng`, `search_duckduckgo` | Discovery only: results are never used as job data. |
| Supplementary | `jobspy` | Optional `python-jobspy`: Indeed and LinkedIn by default (Glassdoor, Bayt, Google can be enabled), one Indeed country per location, LinkedIn descriptions fetched for scoring. |

**Board registry and rotation.** Configured boards are fetched every run. Boards discovered by Common Crawl, search or career pages are stored in R2. Each run checks:

1. "hot" boards: a relevant job in the last 30 days, or discovered this run from a search result or career page;
2. a rotating batch of the others (`ROTATION_BOARDS_PER_ATS`, default 60 per ATS), never-checked boards first.

Coverage therefore grows well beyond a fixed company list without hammering any API.

**Board validation.** Every board fetch is classified:

| Status | Meaning |
|---|---|
| `VALID` | The board exists; it may have zero jobs. |
| `INVALID` | The ATS returned 404; the slug is wrong. |
| `EMPTY_UNVERIFIED` | The ATS returns an empty list for unknown boards too, so the bot can't tell whether the board exists (SmartRecruiters, Ashby). |
| `SCHEMA_MISMATCH` | The response didn't have the expected structure. |
| `ERROR` | A network, rate-limit or blocking failure. |

Invalid configured slugs are listed prominently in the run summary and GitHub step summary. An invalid board is never counted as "0 jobs".

### Matching (deterministic, 0–100)

| Category | Max | Evidence |
|---|---|---|
| Title | 40 | direct role 40 · adjacent role 34 · geospatial title 34 · geo term only 28 · generic title 12 |
| Technical skills | 25 | ArcGIS Pro, QGIS, ArcPy, PyQGIS, Python, SQL, PostGIS, FME, AutoCAD, MicroStation, TerraScan, Smallworld, Metashape, Arcade, GeoJSON, spatial databases, REST APIs, plus GDAL, GeoPandas, GEE, ENVI, Pix4D… |
| Domain | 20 | GIS, geomatics, cartography, surveying, cadastral, land/mineral tenure, LiDAR, point clouds, photogrammetry, remote sensing, UAV, utility mapping, telecom/fiber, electric distribution… |
| Responsibilities | 10 | digitizing, georeferencing, map production, QA/QC, coordinate systems, field data collection… |
| Location | 5 | remote scope / preferred locations |

- **Tiers.** ≥70 is *High*, ≥55 is *Possible*, anything lower is rejected.
- **Canonical names.** Variants collapse to one name (`Esri`/`ArcGIS Online` → ArcGIS; `ArcGIS Pro` is never double-counted as ArcGIS), and each skill counts once.
- **Required vs preferred.** Detected from the sentence the skill appears in, e.g. "ArcGIS Pro required".
- **Generic titles** (Analyst, Technician, Engineer, Data Analyst…) need **≥2 distinct geospatial signal families**. "GIS" and "geospatial" are one family.
- **Negative titles** (Director, VP, Recruiter, Nurse, Electrical Engineer, …) are rejected. "Architect" is overridden when the title is geospatial ("GIS Architect"). "Engineer" is never excluded globally.
- **Title-only sources.** A strong geospatial title with no description available is floored at *Possible* and labelled "Title-only evidence".
- **Evidence-only explanations.** "Why it matched" bullets are generated only from terms actually found in the job text.
- **Internships.** Intern, co-op, trainee, apprentice, working-student titles and their French, German and Spanish forms (stagiaire, stage, PFE, alternance, Werkstudent, prácticas…) are rejected. `EXCLUDE_INTERNSHIPS=false` or `/interns on` brings them back.
- **Work authorisation.** Postings that require an existing right to work, citizenship or permanent residency, a security clearance, or that state no visa sponsorship is offered (English and French wording) are rejected with `WORK_AUTHORIZATION_REQUIRED`, unless the job is in one of `HOME_COUNTRIES` (default `Tunisia`) or the posting says sponsorship is available, in which case "Visa sponsorship offered" appears in the evidence and the digest shows 🛂. Disable with `EXCLUDE_WORK_AUTH_REQUIRED=false`.
- **Rejection codes:** `NO_RELEVANT_TITLE`, `NEGATIVE_TITLE`, `INSUFFICIENT_GEOSPATIAL_SIGNALS`, `LOW_TECHNICAL_RELEVANCE`, `LOCATION_MISMATCH`, `WORK_AUTHORIZATION_REQUIRED`, `LOW_SCORE`.
- **French postings.** Titles such as "Ingénieur SIG", "Géomaticien", "Topographe", "Cartographe" or "Chargé d'études SIG" and French skill and domain wording (télédétection, photogrammétrie, nuages de points, levés topographiques, "maîtrise de QGIS exigée"…) are recognised alongside English, so Tunisian, Maghreb and Québec sources score properly. Accents are folded before title matching.
- **Customising.** Edit `geojobbot/matching/profile.py` to add roles, skills, domains or negatives.

### Deduplication and source fusion

Each observation yields identity keys, strongest first:

1. **native ATS id** — `greenhouse:123`, `lever:<uuid>`, `workday:tenant:R123`…, also recovered from URLs such as `?gh_jid=123`;
2. **source job id**;
3. **canonical URL** — tracking parameters stripped;
4. **content hash**.

Observations sharing a key become one job. A fuzzy key (normalised company + title + place) merges aggregator copies, but it can never merge two *different* ATS postings. Stored records keep all aliases, so a job keeps one canonical ID across runs and sources.

**Field fusion.** Fields come from the most authoritative source: ATS API > employer page > government > feed > aggregator > search. JSON-LD ranks above HTML fallback. The apply link prefers the employer/ATS URL over aggregators.

**Change detection.** A job is updated only by an equal-or-higher-authority source, or by a source that finally provides a description. Changes are recorded per job. Re-alerting on changes is opt-in (`ALERT_ON_CHANGES=true`).

### Alert rules

A job is alerted only when **all** of these hold:

- it is High (or Possible with `NOTIFY_POSSIBLE=true`);
- it was seen live this run;
- it hasn't been notified yet;
- it has retry budget left;
- it isn't stale. The age limit is 15 days (`MAX_JOB_AGE_HOURS=360`, also `ROTATION_MAX_JOB_AGE_HOURS` for rotating discovered boards). The same window drives each source's own "posted within" filter (JobSpy, Adzuna, USAJOBS, JSearch). A job is never rejected just for lacking a posting date.

Delivery rules:

- **Format.** By default each run sends one numbered digest (`ALERT_FORMAT=digest`): High matches first, then Possible, one entry per job with company, score, location, date, salary, top skills and the apply link. It is split into several messages only when it exceeds Telegram's 4096-character limit. `ALERT_FORMAT=individual` sends one message per job instead.
- `notified=true` is written only after Telegram confirms delivery (per message, so every job in a delivered digest part is marked). A failed send is retried on later runs, up to `MAX_NOTIFY_ATTEMPTS`.
- State is checkpointed to R2 *before* alerts are sent, so a crash can't cause a flood of repeats.
- Alerts per run are capped (`MAX_ALERTS_PER_RUN`, default 50), highest scores first; the rest wait for the next run.

### Talking to the bot (Telegram commands)

The bot answers messages from the configured chat only; every other chat is ignored. There is no server: pending messages are handled at the start of each scraper run and by `.github/workflows/commands.yml` (hourly by default; each poll bills about one Actions minute, so use `*/10 * * * *` only on a public repository). Preferences are stored in `state/prefs.json`, separate from the state document, so the poller and the scraper never conflict.

| Command | Effect |
|---|---|
| `/jobs [n]`, `/high [n]` | best current matches that are fresh, not muted, hidden or applied |
| `/search words` (or plain text) | search stored matches by title, company, place, skill |
| `/why code` | score breakdown, evidence, sources and link for one job |
| `/applied code`, `/applied` | mark as applied (never alerted again) · list applications |
| `/hide code`, `/unhide code` | dismiss or restore a job |
| `/mute text`, `/unmute text`, `/muted` | silence a company or title word |
| `/threshold 70 55` | High and Possible cut-offs (from the next run) |
| `/locations Canada, Tunisia`, `/locations reset` | preferred locations |
| `/interns on\|off` | include or exclude internships |
| `/pause`, `/resume` | hold alerts (jobs are still collected and released on resume) |
| `/status`, `/run`, `/help` | last run and settings · start a scraper run now · this list |

`code` is the 5-character tag printed next to every job in a digest.

---

## 2. Persistent state in R2

There is no SQLite and no GitHub cache. A single gzipped JSON document is simpler, atomic per PUT, and small: roughly 1 MB compressed for ~10k tracked jobs. Jobs with clearly irrelevant titles are counted but not stored, and old rejected records are pruned.

```
state/state.json.gz                         authoritative state (jobs, boards, sources, cursors, page cache)
state/prefs.json                            preferences set from Telegram (mutes, applied, hidden, thresholds, pause)
state/backups/<timestamp>-<run>.json.gz     previous versions (STATE_BACKUPS_KEEP, default 20)
state/conflicts/<run>.json.gz               state that could not be saved because another writer changed it
runs/<YYYY-MM-DD>/<run>.json.gz             full run report + per-job match diagnostics (RUN_RETENTION_DAYS=90)
runs/latest.json                            latest run summary
raw/<YYYY-MM-DD>/<run>/<source>.jsonl.gz    raw normalised snapshots (RAW_RETENTION_DAYS=14)
```

Safety rules:

- The document is decoded again after serialisation, before any upload.
- The previous version is copied to `backups/` before each overwrite.
- **Optimistic concurrency:** if the state's ETag changed since load, the write goes to `conflicts/` and the run fails instead of clobbering data. The workflow's `concurrency` group normally prevents this anyway.
- A missing state object while backups exist restores the newest valid backup; the bot never silently starts empty. Corrupt state also falls back to backups. If none are usable, or the state is missing while `runs/` reports exist, the run stops (exit 1) unless `ALLOW_STATE_RESET=true`.
- Every save is read back (`HEAD`) before the run continues; a write that is not visible afterwards fails the run.
- If R2 is unreachable, the run aborts **before** scraping or alerting (exit code 1).
- Old `raw/` and `runs/` objects are deleted once a day.

Set `STATE_PREFIX` to keep several bots in one bucket.

---

## 3. Setup

### 3.1 Cloudflare R2

1. Cloudflare dashboard → **R2 Object Storage** → **Create bucket**, e.g. `geospatial-job-bot`. The default location and Standard storage class are fine.
2. **R2 → Manage R2 API Tokens → Create API token**
   - Permissions: **Object Read & Write**
   - Specify bucket: only `geospatial-job-bot`
   - Create, then copy the **Access Key ID** and **Secret Access Key**. The secret is shown once.
3. Your endpoint is `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`. The account ID is shown on the R2 overview page.

Usage is a few dozen operations per run, far inside R2's free tier for a 4-hourly schedule.

### 3.2 Telegram

1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts → copy the **bot token**.
2. Send any message to your new bot. For a group, add the bot to the group and send a message there.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy `result[].message.chat.id`. Group IDs are negative, e.g. `-100123…`.

### 3.3 GitHub repository

Push this project to a repository. Then **Settings → Secrets and variables → Actions**.

**Secrets** (required):

| Name | Value |
|---|---|
| `R2_ENDPOINT_URL` | `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` |
| `R2_ACCESS_KEY_ID` | R2 token access key |
| `R2_SECRET_ACCESS_KEY` | R2 token secret |
| `R2_BUCKET_NAME` | `geospatial-job-bot` |
| `TELEGRAM_BOT_TOKEN` | BotFather token |
| `TELEGRAM_CHAT_ID` | chat id |

Optional secrets, each enabling one more source:

| Name | Where to get it |
|---|---|
| `USAJOBS_API_KEY`, `USAJOBS_EMAIL` | free key from developer.usajobs.gov |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | free at developer.adzuna.com (Canada, UK, US, AU, DE, FR… — not Tunisia) |
| `JOOBLE_API_KEY` | free at jooble.org/api/about (covers Tunisia, the Maghreb and Canada) |
| `JSEARCH_API_KEY` | free tier at rapidapi.com (JSearch, 200 requests/month): Google for Jobs results, i.e. LinkedIn, Indeed and Glassdoor postings with full descriptions, any country |

Optional **variables** (`scraper.yml` applies sensible defaults when unset; Canada and Tunisia are the default locations):

| Name | Purpose |
|---|---|
| `PREFERRED_LOCATIONS` | default `Canada,Tunisia,Tunisie,Tunis` |
| `ACCEPTED_REMOTE_SCOPES` | default `Worldwide,EMEA,Africa,Americas` |
| `JOBSPY_SITES` | default `indeed,linkedin`; add `glassdoor`, `bayt`, `google` at your own risk |
| `JOBSPY_LOCATIONS` | default `Remote@usa,Canada@canada,Tunisia@worldwide` (`@country` picks the Indeed site; `worldwide` skips Indeed/Glassdoor) |
| `JOOBLE_LOCATIONS`, `ADZUNA_COUNTRIES` | default `Canada,Tunisia` and `ca,gb,us` |
| `ALERT_FORMAT` | `digest` (default) or `individual` |
| `DUCKDUCKGO_ENABLED` | default `false` in Actions (DuckDuckGo serves a bot check to runners) |
| `SEARXNG_URL` | your own SearXNG instance |
| `BOT_USER_AGENT` | include your contact URL or email |

Or with the GitHub CLI:

```bash
gh secret set R2_ENDPOINT_URL      --body "https://<ACCOUNT_ID>.r2.cloudflarestorage.com"
gh secret set R2_ACCESS_KEY_ID     --body "<key id>"
gh secret set R2_SECRET_ACCESS_KEY --body "<secret>"
gh secret set R2_BUCKET_NAME       --body "geospatial-job-bot"
gh secret set TELEGRAM_BOT_TOKEN   --body "<token>"
gh secret set TELEGRAM_CHAT_ID     --body "<chat id>"
gh variable set BOT_USER_AGENT     --body "GeospatialJobBot/1.0 (+https://github.com/<you>/<repo>)"
```

**Workflows:**

- `.github/workflows/scraper.yml` runs every 4 hours (`0 */4 * * *`). You can also start it manually, optionally as a dry run. It uses `concurrency: geospatial-job-scraper` without cancel-in-progress, so runs never overlap.
- **Never use "Re-run jobs"** to run the bot after a push: GitHub replays the commit the original run was created from. The workflow now refuses such runs with an error. Use **Run workflow** instead, or wait for the schedule. Every summary shows the commit it ran (`code abc1234`).
- `.github/workflows/tests.yml` runs the offline tests on every push. Started manually, it can also run the live integration tests and validate your sources.

---

## 4. Running the live R2 and Telegram integration tests (GitHub Actions)

After the secrets are set:

```bash
# 1. run offline tests + live R2/Telegram integration tests + CLI self-tests
gh workflow run tests.yml -f live=true

# 2. follow it
gh run watch "$(gh run list --workflow=tests.yml --limit 1 --json databaseId -q '.[0].databaseId')"

# 3. read the logs (look for "PASSED" lines and "PASS: message delivered")
gh run view "$(gh run list --workflow=tests.yml --limit 1 --json databaseId -q '.[0].databaseId')" --log | grep -E "PASS|FAIL|passed|failed"
```

In the web UI: **Actions → Tests → Run workflow → tick "Also run live…" → Run**.

The live R2 tests:

- write only under `selftest/<random>/` and delete everything they create;
- check object put/get/head/copy/list/delete;
- simulate a brand-new runner recovering state;
- verify that concurrent-write detection works.

The Telegram test sends one sample alert, and the CLI self-test sends one short confirmation message. The same manual run also executes `validate-sources` and prints the status of every configured board.

Then do a production **dry run**, which prints alerts and writes nothing:

```bash
gh workflow run scraper.yml -f dry_run=true
```

Finally, run it for real: `gh workflow run scraper.yml`. After that the cron schedule takes over.

---

## 5. Local usage

Requires Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -r requirements-optional.txt   # optional JobSpy

python -m pytest -q                          # offline test suite

# local dry run: without R2 variables, state goes to ./.state (never used in Actions)
DRY_RUN=true python -m geojobbot run

# local run against real R2 without sending Telegram messages
export R2_ENDPOINT_URL=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET_NAME=...
DRY_RUN=true python -m geojobbot run

python -m geojobbot validate-sources                          # exit 3 if any configured source is invalid
python -m geojobbot diagnose "https://boards.greenhouse.io/<company>/jobs/<id>"   # why did/didn't this match?
python -m geojobbot score --title "GIS Technician" --description-file job.txt --location "Remote - Canada"
python -m geojobbot inspect                                   # state overview (reads R2 if configured)
python -m geojobbot inspect --job "lidar"                     # search stored jobs
python -m geojobbot inspect --boards INVALID                  # boards the ATS says do not exist
python -m geojobbot inspect --run diag --grep "cartographer"  # match diagnostics from the latest run
python -m geojobbot selftest-r2
python -m geojobbot selftest-telegram
```

In a dry run, alerts are printed rather than sent and no state is written, unless `DRY_RUN_WRITE_STATE=true`. Written this way, jobs remain un-notified and will alert on the next real run.

---

## 6. Configuration

**Sources:** `config/sources.toml`

- ATS slugs per provider. For Workday, paste the career-site URL.
- `[[career_sites]]` with an optional `job_url_pattern`, `sitemap` and `max_pages`. Add `source_type = "feed"` for a job board's search page (shipped: Keejob keyword searches): its postings then carry feed authority in fusion and the board's name is never used as the employer.
- `[[rss_feeds]]` (shipped: GoGeomatics, GISjobs.com, Job Bank Canada searches, Tunisie Travail searches). Job Bank's feed matches occupation titles, not free text: "surveyor" and "geomatics" return results, "GIS" does not.
- extra search queries

> Every ATS slug shipped in `sources.toml` was checked against the live public APIs on 2026-09-16 (the file lists the job count per board). Companies change ATS providers, so run `validate-sources` or check the run summary after editing and fix or remove anything reported `INVALID`. SmartRecruiters and Workable answer with an empty list for unknown accounts, so confirm a non-empty board before adding one there.

**Environment variables** (all optional unless noted):

| Variable | Default | Meaning |
|---|---|---|
| `DRY_RUN` | `false` | print alerts, don't send, don't write state |
| `DRY_RUN_WRITE_STATE` | `false` | write state during a dry run |
| `REQUIRE_R2` | `false` (`true` in Actions) | fail instead of falling back to local state |
| `HIGH_MATCH_THRESHOLD` / `MEDIUM_MATCH_THRESHOLD` | `70` / `55` | tier thresholds |
| `NOTIFY_POSSIBLE` | `true` | alert on Possible matches |
| `MAX_JOB_AGE_HOURS` / `ROTATION_MAX_JOB_AGE_HOURS` | `360` / `360` | freshness limits (15 days) |
| `MAX_ALERTS_PER_RUN` / `MAX_NOTIFY_ATTEMPTS` | `50` / `5` | alert flood control and retry budget |
| `ALERT_ON_CHANGES` | `false` | re-alert when a notified job's title/location/salary/remote status changes |
| `ALERT_FORMAT` | `digest` | `digest`: one numbered list per run · `individual`: one message per job |
| `PREFERRED_LOCATIONS`, `ACCEPTED_REMOTE_SCOPES` | empty | location scoring |
| `STRICT_LOCATION_FILTER` | `false` | reject non-matching locations |
| `EXTRA_NEGATIVE_TITLES` | empty | comma-separated extra exclusions |
| `EXCLUDE_INTERNSHIPS` | `true` | reject internships, co-ops, traineeships and student jobs |
| `EXCLUDE_WORK_AUTH_REQUIRED`, `HOME_COUNTRIES` | `true`, `Tunisia` | reject postings needing existing work authorisation / no sponsorship, except in home countries |
| `ROTATION_BOARDS_PER_ATS` | `60` | discovered boards checked per ATS per run |
| `MAX_DETAIL_FETCHES_PER_BOARD` | `30` | per-job detail requests per board |
| `GENERIC_PAGES_PER_RUN` / `GENERIC_PAGES_PER_HOST` / `PAGE_REFETCH_DAYS` | `80` / `15` / `7` | generic crawler budget |
| `COMMONCRAWL_ENABLED` / `COMMONCRAWL_PAGES_PER_RUN` | `true` / `2` | Common Crawl board discovery |
| `SEARCH_QUERIES_PER_RUN` | `8` | rotating search queries |
| `SEARXNG_URL` | empty | enables SearXNG backend |
| `DUCKDUCKGO_ENABLED` | `true` | DuckDuckGo HTML backend (obeys robots.txt) |
| `FEEDS_ENABLED` | `remotive,jobicy,himalayas,arbeitnow,remoteok` | which public feeds run |
| `JOBSPY_ENABLED`, `JOBSPY_SITES`, `JOBSPY_LOCATIONS`, `JOBSPY_TERMS_PER_RUN` | `true`, `indeed,linkedin`, `Remote`, `3` | JobSpy; locations accept `Location@indeed_country` |
| `JOBSPY_RESULTS_WANTED`, `JOBSPY_LINKEDIN_FETCH_DESCRIPTION` | `15`, `true` | results per search; fetch LinkedIn descriptions (one request per job; set `false` if LinkedIn answers 429) |
| `USAJOBS_API_KEY`, `USAJOBS_EMAIL` | empty | enables USAJOBS |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `ADZUNA_COUNTRIES` | empty, empty, `ca,gb,us` | enables Adzuna |
| `JOOBLE_API_KEY`, `JOOBLE_LOCATIONS` | empty, `PREFERRED_LOCATIONS` | enables Jooble |
| `JSEARCH_API_KEY`, `JSEARCH_REQUESTS_PER_RUN`, `JSEARCH_QUERIES` | empty, `1`, six `query@country` entries for CA/TN/US/FR | enables JSearch; queries rotate across runs to stay inside the free quota |
| `DISABLED_BACKENDS` | empty | e.g. `search_duckduckgo,arbeitnow` |
| `SOURCE_CONCURRENCY` | `6` | parallel backends |
| `RUN_TIME_BUDGET_MINUTES` | `40` | soft deadline; backends stop early and report PARTIAL |
| `DEFAULT_HOST_DELAY`, `HTTP_TIMEOUT` | `1.5`, `30` | politeness and timeouts |
| `STATE_PREFIX`, `STATE_BACKUPS_KEEP`, `ALLOW_STATE_RESET` | empty, `20`, `false` | state storage |
| `RAW_RETENTION_DAYS`, `RUN_RETENTION_DAYS`, `STORE_RAW_SNAPSHOTS` | `14`, `90`, `true` | artifacts |
| `REJECTED_RETENTION_DAYS`, `JOB_RETENTION_DAYS` | `30`, `180` | pruning |
| `BOT_USER_AGENT` | descriptive default | set to include your contact |
| `LOG_LEVEL` | `INFO` | logging |

---

## 7. Operations guide

- **Read the run summary** at the end of the job log or on the run's summary page. It shows per-source status, invalid configured sources, raw/unique/new/seen/updated counts, match tiers, alert results, R2 load/save status, HTTP retry/error counts and parser errors.
- **Exit codes.**
  - `0` — normal. Individual source failures are reported, not fatal.
  - `1` — state could not be loaded or saved (no alerts sent if loading failed).
  - `2` — every enabled source failed, usually a network problem.
- **A job you expected didn't arrive.** Run `python -m geojobbot diagnose <url>` for the full breakdown and rejection reasons, or `inspect --run diag --grep "<title>"` to see how it scored in the last run.
- **Invalid slugs** are listed each run. Use `inspect --boards INVALID` to see all of them, including discovered boards, which are re-checked after 60 days.
- **Too many or too few alerts.** Adjust the thresholds, `NOTIFY_POSSIBLE`, `EXTRA_NEGATIVE_TITLES`, or the location variables. Add missing skills or roles in `profile.py`.
- **Recovering state.** Copy an object from `state/backups/` over `state/state.json.gz`, e.g. with `rclone` or `aws s3 cp --endpoint-url $R2_ENDPOINT_URL`. If a `conflicts/` object appears, two writers overlapped: inspect both, keep the one you want and delete the other.
- **Resetting everything.** Delete `state/` in the bucket (backups included) **and** `runs/`, or set `ALLOW_STATE_RESET=true` for one run: a missing state while `runs/` still holds reports is treated as data loss and the run refuses to start fresh, because doing so would re-alert every job. The next run is then a first run, with alerts capped by `MAX_ALERTS_PER_RUN`.
- **Adding a site-specific extractor.** Call `register_site_adapter(host_regex, func)` in `geojobbot/scrapers/generic.py`.
- **Adding a new ATS.** Implement `ATSAdapter.fetch_board` (optionally `fetch_job`), add URL detection in `scrapers/ats/detect.py`, and register it in `all_adapters()`.

---

## 8. Honest limitations

- **Live APIs were not reachable during development.** All adapters are tested against mocked responses built from the public API formats. Greenhouse, Lever, Ashby and Himalayas formats were checked against documentation. Workable (widget API), Recruitee, Personio, Jobicy, Arbeitnow, RemoteOK and USAJOBS parsers are based on their known public formats but have not been exercised live. If a provider has changed its format, that source reports `SCHEMA_MISMATCH`/`FAILED` rather than silently returning nothing.
- **Search discovery is weak by design.** Google and Bing can't be scraped legitimately. DuckDuckGo's HTML endpoint is likely disallowed by robots.txt; the bot will then report `ROBOTS_DISALLOWED` and stop. SearXNG works only with an instance you run. Common Crawl lags the live web by weeks: it finds *companies* (boards), not fresh postings, which the rotation then checks live. `index.commoncrawl.org/robots.txt` disallows everything but `collinfo.json`; because the CDX index is a query API published for programmatic use rather than a site to crawl, this is the one place the bot does not apply robots.txt. Set `COMMONCRAWL_ENABLED=false` if you disagree. The index server is often overloaded and returns 5xx; the backend then reports `FAILED` for that run and tries again next time.
- **Rotation is slow at scale.** With tens of thousands of discovered boards, a full pass takes days to weeks. Boards that produce relevant jobs are promoted to every run.
- **No JavaScript rendering.** Pages that load jobs only through client-side JavaScript, without JSON-LD or embedded JSON, can't be extracted. Workday is handled through its public JSON endpoints.
- **Blocked sites stay blocked.** No CAPTCHA solving, login, stealth browsers or proxies are used. Such failures are recorded and skipped.
- **JobSpy** depends on third-party sites that may rate-limit or block, and each site's terms apply to you (LinkedIn's robots.txt disallows its guest job search; JobSpy uses it anyway, which is why it is a separate, optional package). Indeed and LinkedIn work from GitHub runners; Glassdoor and Bayt frequently answer 400/403 and are off by default. Indeed has no Tunisian site, so Tunisia is searched on LinkedIn only.
- **LinkedIn and Glassdoor** have no public job API. There is no legitimate way to read them beyond JobSpy's best-effort scraping above.
- **Fuzzy deduplication** can very occasionally merge two genuinely different postings with identical company, title and place that don't come from ATS APIs (for example, two identical openings on a generic careers page).
- **Matching is conservative.** Jobs whose titles aren't recognisably geospatial are rejected even when the description is strongly geospatial (e.g. "Software Engineer, Maps"). Extend `GEO_TITLE_TERMS` or `DIRECT_ROLES` if you want those.
- **Posting dates** from JSON-LD are only as reliable as the site. Some sites re-stamp `datePosted` on every render, which can make an old job look fresh.
- **Concurrent writers.** R2 has no multi-object transactions. Concurrency protection is ETag comparison plus the workflow's concurrency group; a narrow race between check and write remains possible if you run the bot from two places at once.

---

## 9. Project layout

```
geojobbot/
  config.py              settings from env + config/sources.toml
  models.py              RawJob, JobRecord, SourceResult, source priorities
  main.py                CLI (run, validate-sources, diagnose, score, inspect, self-tests)
  core/pipeline.py       orchestration, isolation, alerts, artifacts
  core/fusion.py         identity keys, grouping, field fusion
  core/jobs.py           state merge, change detection, alert selection, pruning
  core/boards.py         board registry and rotation scheduler
  core/report.py         run summary, GitHub step summary, diagnostics
  matching/profile.py    roles, skills, domains, negatives (edit me)
  matching/matcher.py    deterministic scorer
  scrapers/base.py       Backend interface, RunContext, page queue
  scrapers/ats/          ATS detection + 8 adapters
  scrapers/generic.py    JSON-LD / embedded JSON / HTML extractor
  scrapers/pages.py      career sites, sitemaps, generic page backend
  scrapers/search.py     SearXNG, DuckDuckGo, Common Crawl
  scrapers/feeds.py      public feeds, RSS, USAJOBS, JobSpy
  storage/               ObjectStore, R2Store, LocalStore, StateManager
  notifications/telegram.py
  utils/                 http (retries/rate limits), robots, urls, location, dates, text
config/sources.toml
tests/unit/              offline tests (mock HTTP, fake R2, fake Telegram)
tests/integration/       live tests (RUN_LIVE_TESTS=1)
.github/workflows/       scraper.yml, tests.yml
```
# geospatial-job-bot
