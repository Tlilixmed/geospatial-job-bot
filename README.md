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
| Geospatial boards | `career_sites` (GEO CAREERS), `rss_feeds` (GoGeomatics, GISjobs.com) | GEO CAREERS is the largest geospatial-only board: 700+ postings with structured data, read through its sitemap newest-first. |
| Official employment services | `bundesagentur`, `francetravail` | Germany's public job API (no key; German-language postings skipped) and France Travail (free key). |
| Community | `hn_hiring` | Hacker News "Who is hiring?" monthly thread through the free Algolia API, once a day, strict geospatial filter. |
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
- **Weak titles.** "Surveyor", "surveying", "mapping", "topographe", "géomètre", "arpenteur" and "مساح" are geospatial only in context. Quantity, building, marine, insurance and chartered surveyors, survey researchers and process/data mapping roles are excluded outright; any other title whose only geospatial signal is one of these words must show geomatics evidence in the description (GNSS, total station, land/topographic/cadastral survey, Civil 3D, point clouds, or another geospatial family such as GIS or LiDAR), and gets no title-only benefit of the doubt unless it is an explicit role such as "Survey Technician" or "Topographe".
- **Arabic postings.** Titles such as مهندس نظم معلومات جغرافية or فني مساحة and the main domain terms are recognised (hamza forms are folded).
- **Sponsorship.** A posting that offers visa sponsorship gets the full location score wherever it is.
- **Internships.** Intern, co-op, trainee, apprentice, working-student titles and their French, German and Spanish forms (stagiaire, stage, PFE, alternance, Werkstudent, prácticas…) are rejected. `EXCLUDE_INTERNSHIPS=false` or `/interns on` brings them back.
- **Work authorisation.** Postings that require an existing right to work, citizenship or permanent residency, a security clearance, or that state no visa sponsorship is offered (English and French wording) are rejected with `WORK_AUTHORIZATION_REQUIRED`, unless the job is in one of `HOME_COUNTRIES` (default `Tunisia`) or the posting says sponsorship is available, in which case "Visa sponsorship offered" appears in the evidence and the digest shows 🛂. Disable with `EXCLUDE_WORK_AUTH_REQUIRED=false`.
- **Rejection codes:** `NO_RELEVANT_TITLE`, `NEGATIVE_TITLE`, `INSUFFICIENT_GEOSPATIAL_SIGNALS`, `LOW_TECHNICAL_RELEVANCE`, `LOCATION_MISMATCH`, `WORK_AUTHORIZATION_REQUIRED`, `LOW_SCORE`.
- **French postings.** Titles such as "Ingénieur SIG", "Géomaticien", "Topographe", "Cartographe" or "Chargé d'études SIG" and French skill and domain wording (télédétection, photogrammétrie, nuages de points, levés topographiques, "maîtrise de QGIS exigée"…) are recognised alongside English, so Tunisian, Maghreb and Québec sources score properly. Accents are folded before title matching.
- **Customising.** Edit `geojobbot/matching/profile.py` to add roles, skills, domains or negatives.

### AI second opinion (optional, free)

With a `CLOUDFLARE_AI_TOKEN` secret the bot asks Cloudflare Workers AI (`@cf/meta/llama-3.1-8b-instruct`, free daily allowance) about every job the deterministic scorer accepted, once per job, best matches first, at most `AI_REVIEWS_PER_RUN` (40) per run (a backlog of older matches is worked off as they are seen again; `/status` and `/ai` show the coverage):

- a one-sentence English **summary** of what the job is and why it fits (French, Arabic or German postings are translated), shown as 💡 in the digest;
- **concerns** the keyword rules cannot see ("requires 8+ years", "German required", "licensed surveyor only"), shown as ⚠️;
- a 0-10 **fit**, minimum years, required languages, sponsorship reading and up to five key requirements, all visible in `/why code`;
- `/pitch code` drafts a short application note in the posting's language from those requirements and your profile.

The scorer stays the authority. The only decision the model can take is a veto of a *Possible* match it rates clearly irrelevant (fit ≤ 2, `AI_VETO_POSSIBLE=false` disables it); High matches are never vetoed. Every field is validated and clamped, the run never depends on the service (three failures and it stops calling), and an outage changes nothing about alerts. The candidate profile the model sees is the generic one in `geojobbot/ai/review.py`; override it privately with the `CANDIDATE_PROFILE` secret. The account id is taken from the R2 endpoint, so the token is the only thing to add: Cloudflare dashboard → My Profile → API Tokens → Create Token → template **Workers AI** (Read is enough).

### Official visa-sponsor registers

A posting rarely says whether the employer can sponsor a visa, but governments publish who can. Once a week the bot downloads five official registers and keeps the normalised employer names in `reference/sponsors.json.gz` (about 180,000 names, under 3 MB):

- 🇬🇧 the UK Home Office *Register of licensed sponsors* (Worker routes);
- 🇨🇦 Canada's quarterly *positive LMIA employers* list, including the occupations each employer was approved for, so an employer that already hired surveyors, cartographers or geomatics technicians abroad is marked;
- 🇳🇱 the Dutch IND *recognised sponsors* register;
- 🇮🇪 Ireland's DETE list of *companies issued employment permits*, this year and last, with the number of permits (Ireland has no sponsor licence: this is who actually hires from abroad);
- 🇩🇰 Denmark's SIRI list of *companies certified for the Fast-track scheme*.

Accepted jobs are matched by exact normalised name, then by a conservative "core name" variant (legal suffixes removed, at least four characters; variants are labelled so you can check). A hit adds a small bonus (+4, +6 when the Canadian record shows geomatics occupations) **only when the register is the job's own country**, shows as a badge in the digest and in `/why code`, and `/sponsors` lists all current matches with sponsorship evidence: postings that say so first, then licensed employers. A register that fails to download keeps its previous copy. `SPONSOR_REGISTERS=false` turns it off.

### Learning from what you do

`/applied`, `/outcome` and `/hide` are labels. After at least four of them the bot compares the words in titles, companies, skills and domains of what you pursue with what you dismiss and nudges new scores by at most **+6 / −8** points (interviews and offers weigh more than applications). It never rescues a rejected job, never touches hard filters, is applied once per job, and is fully visible: `/why code` shows the nudge and its reasons, `/learning` shows what was learned, `/learning off|on|reset` controls it. A snapshot of each labelled job is kept in the preferences, so learning survives pruning.

### Visa routes: is a work visa realistic for this job?

The sponsor badge says an employer *can* sponsor. `/visa` puts the facts of each accepted job next to the rules of its country's main work-visa route, kept in `config/visa_paths.toml` with a source link per route and an `as_of` date (the figures were read from the official pages on 2026-09-18; the bot warns once they are a year old):

- **employer**: on the official register of that country, or the posting offers sponsorship (✓), says it does not (✗), or is silent (?);
- **salary**: the posted salary, parsed from free text in any of the formats the sources produce, against the legal minimum: UK Skilled Worker £41,700 (£33,400 new entrants), EU Blue Card Germany €50,700 (€45,934 for shortage occupations, which include surveyors and cartographers), Netherlands highly skilled migrant €5,942 a month (€4,357 under 30), France carte bleue €59,373, Ireland Critical Skills €40,904 / €68,911, Denmark Pay Limit DKK 552,000, Australia Core Skills A$79,499. A salary between a reduced and the standard minimum is a "maybe", not a pass;
- **occupation**: graduate-level routes flag technician titles; Australia lists Surveyor, Cartographer and Other Spatial Scientist;
- **routes your own profile opens** (`MY_LANGUAGES`, `HOME_COUNTRIES`): in Canada, *Francophone Mobility* means a French speaker needs **no LMIA** for a job outside Québec; in France, *Géomètre*, *Dessinateur du BTP*, *Chargé d'études techniques du BTP* and *Informaticien d'étude* are on the 2008 France–Tunisia list of occupations open to Tunisians **without the labour-market test**;
- Gulf states: employer-sponsored as a matter of course. US H-1B and Swiss quotas are marked *hard from abroad*.

The verdict (🟢 looks open · 🟡 possible, facts missing · 🟠 hard · ⛔ blocked) annotates digests and `/why code` and orders `/visa`. It never adds a rejection, but a route that is *hard* or *blocked* with nothing saying the employer sponsors costs the job `VISA_PENALTY` points (default 12, `0` = label only): in a live run 13 of 33 High matches were US jobs reachable only through the H-1B lottery, outranking jobs with an open route. The deduction is shown in `/why`, and is lifted by itself when a register hit or the AI review later shows sponsorship. Unknown facts are never punished. `/visa code` explains one job, `/visa france` a country. It is indicative, not legal advice. `VISA_PATHS=false` turns it off.

### Going where sponsorship is proven: prospects and the watch list

Registers and signals are also a *source*. Each run the prospector takes a few employers (`PROSPECTS_PER_RUN`, default 10): your watch list first, then firms that just won a geospatial contract, Canadian employers whose LMIAs were for surveying and geomatics occupations, and registered sponsors whose name says surveying, geomatics, mapping or LiDAR. It derives the slugs such a company would use and asks the public ATS APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee) whether that board exists. A board is kept only when it has jobs **and** the ATS reports a matching company name, so "Summit Geomatics" never becomes some other Summit. Kept boards are read once a day, and every run while they yield relevant jobs. `/prospects` lists what was found and why each employer was tried.

`/watch Fugro` adds an employer yourself (`/watch https://firm.example/careers Firm` for a careers page the generic crawler should read every run). Possible matches from a watched employer are alerted even when alerts are High-only, with a 👀 badge. `/unwatch name` removes one.

### Deadlines, reposts, interview sheets, speculative applications

- **Deadlines.** "Closing date: 30 September", "date limite de candidature : 05/10/2026", "apply by Oct 2" are read from descriptions (only dates right after a deadline phrase). A High match closing within three days that you neither applied to nor hid gets **one** reminder; a posting past its deadline is no longer listed or alerted.
- **Reposts.** The same title at the same employer seen again three weeks or more after an earlier posting shows `♻️ posted 2× again since Jun 2026`: a role that is hard to fill (a better starting point for sponsorship) or an evergreen advert. Worth knowing before writing.
- **`/prep code`**: an interview sheet. What the bot established (salary, sponsor record, visa route, deadline), then five likely questions with a hint from your own experience, two weak points, three questions to ask, a one-line introduction. Sent by itself when `/outcome code interview` is recorded.
- **`/approach firm`**: a short unsolicited application (*candidature spontanée*, in French for francophone firms) that opens with the reason to write now, such as the contract the firm just won according to `/signals`. `/approach` alone lists those firms.

### What the job bots on GitHub and Reddit do, and what this one does instead

A comparison with the popular open-source job bots (ApplyPilot, job-digest, opportunity-crawler, the LinkedIn Easy-Apply bots) shaped this round:

- **Auto-apply is deliberately absent.** The auto-appliers submit hundreds of applications a day through browser automation, CAPTCHA solvers and your LinkedIn account. That violates the sites' terms, gets accounts banned, and produces applications recruiters recognise and bin. This bot automates *finding* and *deciding*; it drafts (`/pitch`, `/approach`, `/prep`) and you send.
- **Closing the loop.** The bots stop at the alert. Here an application has a life: outcomes (`/outcome`), follow-up reminders, an interview sheet, and, with Cloudflare Email Routing, employer replies that record themselves (`cloudflare/README.md`, step 10).
- **Skills gap per job.** `/why code` and `/prep code` show which of the posting's skills you have and which are not on your list (`/skills`), the deterministic part of what the resume-tailoring bots do with an LLM.
- **Sources they use that were missing here:** GEO CAREERS (sitemap), the Hacker News "Who is hiring?" thread, Germany's public job API. Conservation Job Board and the Geospatial Jobs newsletter were checked and left out: 5 of 181 feed items were geospatial, and the newsletter links to LinkedIn postings that cannot be read.
- **Their good ideas already here:** LLM enrichment with a validated schema, content-hash dedup, a capped digest against decision fatigue, GitHub Actions scheduling, resume-profile scoring.
- **Worth doing by hand:** the monthly Hacker News "Who wants to be hired?" thread (a post there is read by the same companies that post in "Who is hiring?"), and the weekly Geospatial Jobs newsletter (geospatial.substack.com).

### Knowing which sources earn their keep

`/sources` (and a few lines in the weekly summary): per source over 28 days, the raw volume, the accepted jobs it found, how many of those **no other source carried** ("only here": what you would have missed without it) and how many you applied to. It names noise (volume, nothing accepted), redundant sources, and API quotas spent without anything unique.

### Skills radar and market signals

- **`/radar`** (also sent once a month, `MONTHLY_RADAR=false` disables): across the matches of the last 45 days, which skills employers ask for, how often as a hard requirement, and which of those you lack — the gaps worth closing, ordered by demand. `/skills`, `/skills add postgis`, `/skills remove fme` maintain your list (`MY_SKILLS` sets it from the environment).
- **`/signals`** (checked once a day, `MARKET_SIGNALS=false` disables): World Bank-financed procurement notices about geospatial work — *individual consultancies* you could bid for, *contract awards* naming the firm that just won a cadastre, LiDAR or mapping project (firms that win contracts hire), and *tenders* announcing projects. New ones arrive as one short message.

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

- it is High, or Possible with `NOTIFY_POSSIBLE=true` (the shipped workflow sets `false`: alerts are High only, `/possible on` changes it from Telegram, and `/jobs` or `/range` always show the Possible ones);
- it was seen live this run;
- it hasn't been notified yet;
- it has retry budget left;
- it isn't stale. The age limit is 15 days (`MAX_JOB_AGE_HOURS=360`, also `ROTATION_MAX_JOB_AGE_HOURS` for rotating discovered boards). The same window drives each source's own "posted within" filter (JobSpy, Adzuna, USAJOBS, JSearch). A job is never rejected just for lacking a posting date.

Delivery rules:

- **Format.** By default each run sends one numbered digest (`ALERT_FORMAT=digest`): High matches first, then Possible, one entry per job with company, score, location, date, salary, top skills and the apply link. It is split into several messages only when it exceeds Telegram's 4096-character limit. `ALERT_FORMAT=individual` sends one message per job instead.
- `notified=true` is written only after Telegram confirms delivery (per message, so every job in a delivered digest part is marked). A failed send is retried on later runs, up to `MAX_NOTIFY_ATTEMPTS`.
- State is checkpointed to R2 *before* alerts are sent, so a crash can't cause a flood of repeats.
- Alerts per run are capped (`MAX_ALERTS_PER_RUN`, default 20), highest scores first; the rest wait for the next run. The same title at the same company in several places is one entry (`×3`, with each code).

Other messages the bot sends by itself:

- **Follow-ups.** 7 and 21 days after `/applied`, if no outcome was recorded, one reminder asks how it went and suggests a follow-up.
- **Health.** A source failing three runs in a row, its recovery, and newly invalid configured boards are reported once each, not every run. A crashed workflow run sends a Telegram notice too.
- **Weekly summary, monthly radar, new market signals** (each can be disabled).

### Talking to the bot (Telegram commands)

The bot answers messages from the configured chat only; every other chat is ignored. There is no server: pending messages are handled at the start of each scraper run and by `.github/workflows/commands.yml` (hourly by default; each poll bills about one Actions minute, so use `*/10 * * * *` only on a public repository). Preferences are stored in `state/prefs.json`, separate from the state document, so the poller and the scraper never conflict.

| Command | Effect |
|---|---|
| `/jobs [n]`, `/high [n]` | best current matches that are fresh, not muted, hidden or applied |
| `/range 60 70` | fresh jobs whose score lies in a range (includes ones just under the Possible cut-off) |
| `/search words` | search stored matches by title, company, place, skill |
| `/why code` | score breakdown, evidence, AI second opinion, sources and link for one job |
| `/ai [n]` | the AI's view of current matches, best fit first: fit /10, summary, concerns, years, sponsorship, languages |
| `/pitch code` | Workers AI drafts a short application note for that job, in the posting's language |
| `/prep code` | interview sheet for that job (sent by itself when an interview is recorded) |
| `/approach [firm]` | firms with a reason to hire · draft a speculative application to one |
| `/visa`, `/visa code`, `/visa country` | how open the work-visa route looks for current matches · one job · a country's rules |
| `/watch [company or URL]`, `/unwatch name`, `/prospects` | employers to follow closely · employers proven to sponsor whose boards were found |
| `/sources` | which sources deliver, which only make noise |
| `/sponsors [n]` | current matches with sponsorship evidence: the posting says so, or the employer is on an official register |
| `/applied code`, `/applied` | mark as applied (never alerted again) · list applications with their status |
| `/outcome code interview\|offer\|rejected\|withdrawn\|ghosted` | record what happened to an application (feeds the weekly summary and learning) |
| `/hide code`, `/unhide code` | dismiss or restore a job |
| `/mute text`, `/unmute text`, `/muted` | silence a company or title word |
| `/threshold 70 55` | High and Possible cut-offs (from the next run) |
| `/locations Canada, Tunisia`, `/locations reset` | preferred locations |
| `/interns on\|off` | include or exclude internships |
| `/possible on\|off` | alert on Possible matches too, or on High only |
| `/pause`, `/resume` | hold alerts (jobs are still collected and released on resume) |
| `/learning`, `/learning off\|on\|reset` | what the bot learned from your actions · control it |
| `/radar`, `/skills [add\|remove name]` | skills in demand versus yours · edit your skills list |
| `/signals` | recent geospatial contract awards, consultancies and tenders |
| `/weekly` | applications (still listed or gone) and High matches still open; also sent automatically once a week (`WEEKLY_SUMMARY=false` disables) |
| `/status`, `/run`, `/help` | last run and settings · start a scraper run now · this list |

`code` is the 5-character tag printed next to every job in a digest.

**Plain language.** Slash commands are optional. Ordinary sentences in English or French are understood, typos included: "i want the top 5 matching offers", "jobs between 60 and 70", "jobs above 80", "lidar jobs in montreal", "why a3f9c", "i applied to a3f9c", "not interested in a3f9c", "stop showing leidos", "set threshold to 75", "no internships", "pause alerts", "resume", "run now", "what can you do", "montre moi les meilleures offres", "mets le seuil à 75". The reply starts with how the sentence was understood (`↪ /jobs 5`), so a misreading is visible and reversible. Anything that is not an instruction is treated as a search. The rules are deterministic (`geojobbot/notifications/intents.py`). Optionally, the Cloudflare Worker can add a free Workers AI reading of the sentence as a second opinion: it is used only when the rules fall back to a search, is validated against the command list and existing job codes, and is labelled `· AI` in the reply.

**Dashboard.** With a `DASHBOARD_KEY` secret on the Worker, `https://<worker>/dash/<key>` shows the same data in a browser: filterable matches with visa, sponsor, deadline and AI chips, your applications by status, visa routes, employers and sources. Read-only; you act through Telegram.

**Instant replies.** `cloudflare/worker.js` is a Telegram webhook. With its `INBOX` R2 binding it answers most messages itself in under a second: every scraper run publishes `state/index.json` (current matches, codes, scores, AI notes, sponsor hits, run status, the help text), and the Worker reads it together with `state/prefs.json`, which it also updates for `/applied`, `/outcome`, `/hide`, `/mute`, `/threshold`, `/pause` and the other preference commands, in the same schema the Python side uses. `/run`, `/pitch`, `/weekly`, `/radar`, `/skills`, `/learning` and free-text sentences that would change something start the commands workflow instead (reply in about a minute, nothing billed while you are silent). The Worker's own AI reading of a sentence may open read-only views only; the deterministic rules remain the authority for anything that changes a setting. `cloudflare/README.md` has the setup steps; afterwards set the repository variable `TELEGRAM_WEBHOOK=true` so scheduled polling is skipped. **On a public repository the `INBOX` binding is also what keeps your messages private** (step 6): workflow inputs are world-readable, and with the binding they travel through the bucket instead.

---

## 2. Persistent state in R2

There is no SQLite and no GitHub cache. A single gzipped JSON document is simpler, atomic per PUT, and small: roughly 1 MB compressed for ~10k tracked jobs. Jobs with clearly irrelevant titles are counted but not stored, and old rejected records are pruned.

```
state/state.json.gz                         authoritative state (jobs, boards, sources, cursors, page cache)
state/prefs.json                            preferences set from Telegram (mutes, applications and outcomes, hidden, thresholds, pause, skills)
state/index.json                            compact read model for the Cloudflare Worker, rewritten by every run
state/descriptions.json.gz                  descriptions of accepted jobs (6,000 characters each) for /pitch and late AI reviews
reference/sponsors.json.gz                  official sponsor registers (UK, CA, NL, IE, DK), refreshed weekly
inbox/<update_id>.json                      a Telegram message handed from the Worker to the commands workflow (deleted once read)
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
| `RELIEFWEB_APPNAME` | free approved app name from apidoc.reliefweb.int/parameters#appname: UN and NGO GIS / information-management jobs, hired internationally, many in French- or Arabic-speaking duty stations |
| `CLOUDFLARE_AI_TOKEN` | Cloudflare → My Profile → API Tokens → Create Token → **Workers AI** template: AI summaries, concerns, veto and `/pitch` |
| `JSEARCH_API_KEY` | free tier at rapidapi.com (JSearch, 200 requests/month): Google for Jobs results, i.e. LinkedIn, Indeed and Glassdoor postings with full descriptions, any country |

Optional **variables** (`scraper.yml` applies sensible defaults when unset; Canada and Tunisia are the default locations):

| Name | Purpose |
|---|---|
| `PREFERRED_LOCATIONS` | empty by default: any country is acceptable when the employer sponsors; set it to favour some |
| `ACCEPTED_REMOTE_SCOPES` | default `Worldwide,EMEA,Africa,Americas` |
| `JOBSPY_SITES` | default `indeed,linkedin`; add `glassdoor`, `bayt`, `google` at your own risk |
| `JOBSPY_LOCATIONS` | default: Remote, Canada, Tunisia, France, UAE, Saudi Arabia, Qatar, Belgium, Switzerland, Germany, Australia, UK, Morocco, three per run in rotation (`@country` picks the Indeed site; `worldwide` skips Indeed/Glassdoor) |
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
| `NOTIFY_POSSIBLE` | `true` (`false` in the shipped workflow) | alert on Possible matches; `/possible on\|off` overrides it |
| `MAX_JOB_AGE_HOURS` / `ROTATION_MAX_JOB_AGE_HOURS` | `360` / `360` | freshness limits (15 days) |
| `MAX_ALERTS_PER_RUN` / `MAX_NOTIFY_ATTEMPTS` | `20` / `5` | alert flood control and retry budget |
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
| `ADZUNA_REQUESTS_PER_RUN`, `JOOBLE_REQUESTS_PER_RUN`, `JOBSPY_LOCATIONS_PER_RUN` | `30`, `12`, `3` | per-run budgets; countries, locations and terms rotate across runs so quotas and runtime stay flat as the lists grow |
| `RELIEFWEB_APPNAME`, `WEEKLY_SUMMARY` | empty, `true` | enables ReliefWeb · weekly Telegram summary |
| `SPONSOR_REGISTERS` | `true` | UK, Canada, Netherlands, Ireland and Denmark registers: badge, small bonus, `/sponsors` |
| `VISA_PENALTY` | `12` | points a job loses when its visa route is hard or blocked and nothing says the employer sponsors |
| `VISA_PATHS`, `MY_LANGUAGES` | `true`, `English,French,Arabic` | visa-route check (`config/visa_paths.toml`) · languages that open routes such as Francophone Mobility |
| `PROSPECTS_PER_RUN` | `10` | employers looked up on the public ATS APIs per run (`0` = off) |
| `FRANCETRAVAIL_CLIENT_ID`, `FRANCETRAVAIL_CLIENT_SECRET` | empty | enables France Travail (free application at francetravail.io, API *Offres d'emploi v2*) |
| `MONTHLY_RADAR`, `MY_SKILLS` | `true`, built-in list | monthly skills radar · your skills, comma-separated (`/skills` overrides) |
| `MARKET_SIGNALS` | `true` | daily check of World Bank procurement notices for geospatial work |
| `CLOUDFLARE_AI_TOKEN`, `AI_REVIEWS_PER_RUN`, `AI_VETO_POSSIBLE`, `AI_MODEL`, `CANDIDATE_PROFILE` | empty, `40`, `true`, llama-3.1-8b-instruct, built-in | Workers AI second opinion, veto of clearly irrelevant Possible matches, `/pitch` |
| `JSEARCH_API_KEY`, `JSEARCH_REQUESTS_PER_RUN`, `JSEARCH_QUERIES` | empty, `1`, fifteen `query@country` entries (CA, TN, US, FR, BE, CH, DE, GB, AU, AE, SA, QA) | enables JSearch; queries rotate across runs to stay inside the free quota |
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

- **Staying alive without commits.** GitHub disables scheduled workflows after 60 days without repository activity, without telling anyone. Every scraper run re-enables its own schedules through the API, and the Cloudflare Worker's hourly watchdog (`cloudflare/README.md`, step 9) restarts the scraper and tells you when no run has happened for 6 hours. Pushes that only touch documentation, tests or the Worker do not start a scraper run.
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
- **Official APIs.** Germany's Bundesagentur API needs no key and was checked live, but nearly all of its postings are written in German: those are skipped (and remembered), so expect only the occasional English posting from it. France Travail was built from its documented format and tested against mocked responses only; it stays disabled until `FRANCETRAVAIL_CLIENT_ID` and `FRANCETRAVAIL_CLIENT_SECRET` exist.
- **Visa routes are indicative.** The thresholds are dated and sourced in `config/visa_paths.toml`, but occupation eligibility is judged from the title alone and immigration rules change: check the linked official page before relying on a verdict.
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
  core/prefs.py          preferences set from Telegram (state/prefs.json)
  core/health.py         failure-streak, recovery and invalid-board notices
  core/descriptions.py   stored descriptions of accepted jobs
  core/index.py          state/index.json, the read model the Worker answers from
  insights/sponsors.py   official visa-sponsor registers (UK, Canada LMIA, Netherlands)
  insights/learning.py   score nudges learned from applications and hidden jobs
  insights/radar.py      skills in demand versus yours
  insights/signals.py    World Bank procurement: awards, consultancies, tenders
  insights/visa.py       visa-route check against config/visa_paths.toml
  insights/prospects.py  finds the job boards of employers proven to sponsor; watch list
  insights/timing.py     application deadlines, closing-soon reminders, reposts
  insights/yields.py     which sources earn their keep
  ai/                    Workers AI client, job review, /pitch
  matching/profile.py    roles, skills, domains, negatives (edit me)
  matching/matcher.py    deterministic scorer
  scrapers/base.py       Backend interface, RunContext, page queue
  scrapers/ats/          ATS detection + 8 adapters
  scrapers/generic.py    JSON-LD / embedded JSON / HTML extractor
  scrapers/pages.py      career sites, sitemaps, generic page backend
  scrapers/search.py     SearXNG, DuckDuckGo, Common Crawl
  scrapers/feeds.py      public feeds, RSS, USAJOBS, JobSpy
  scrapers/official.py   Bundesagentur (Germany, no key), France Travail (free key)
  scrapers/community.py  Hacker News "Who is hiring?"
  storage/               ObjectStore, R2Store, LocalStore, StateManager
  notifications/         telegram.py (digest), commands.py, intents.py (plain language), weekly.py
  utils/                 http (retries/rate limits), robots, urls, location, dates, text
cloudflare/worker.js     Telegram webhook: instant replies from R2, hand-over to the commands workflow
config/sources.toml
tests/unit/              offline tests (mock HTTP, fake R2, fake Telegram)
tests/worker/            Worker tests (node --test, fake R2 / Telegram / GitHub / AI)
tests/integration/       live tests (RUN_LIVE_TESTS=1)
.github/workflows/       scraper.yml (every 2 hours), commands.yml, tests.yml
```
# geospatial-job-bot
