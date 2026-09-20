# Project review tracker

Started 2026-09-20 at commit `8766868`. 61 Python modules (11,558 lines), one Cloudflare Worker (910 lines), 3 workflows,
280 Python tests, 17 Worker tests.

**How to read this file.** Every part of the system is listed once, in the order data flows through it. Each line gets a
status as the review reaches it: `[ ]` not reviewed · `[ok]` reviewed, nothing to change · `[fixed]` a defect found and
fixed (see Findings) · `[idea]` an enhancement recorded in Findings · `[skip]` deliberately left alone, with the reason.
Findings are numbered `F1, F2…` and carry their evidence, so a fix can be traced back to what was observed.

---

## 1. Pipeline steps, in execution order (`geojobbot/core/pipeline.py`)

| # | Step | Code | Status |
|---|---|---|---|
| 1 | Load state from R2 (backups, ETag, refusal to start fresh) | `storage/state.py` `StateManager.load` | [ ] |
| 2 | Pending Telegram commands, then overlay preferences on settings | `_process_commands`, `core/prefs.py` | [ ] |
| 3 | Build backends (config + watch list + sponsor data for the prospector) | `build_backends` | [ ] |
| 4 | Discovery phase, parallel | `_run_phase("discovery")` | [ ] |
| 5 | Extraction phase, parallel | `_run_phase("extraction")` | [ ] |
| 6 | Raw volume per source recorded | `insights/yields.record_run` | [ ] |
| 7 | Fusion: identity keys, grouping, field authority | `core/fusion.py` | [ ] |
| 8 | Scoring and state merge, change detection, AI veto carried over | `core/jobs.process_fused`, `matching/` | [ ] |
| 9 | Descriptions of accepted jobs kept | `_keep_descriptions`, `core/descriptions.py` | [ ] |
| 10 | Sponsor registers: weekly refresh, lookup, bonus | `_sponsors`, `insights/sponsors.py` | [ ] |
| 11 | Learning nudges | `_learning`, `insights/learning.py` | [ ] |
| 12 | AI second opinion, veto of Possible | `_ai_review`, `ai/` | [ ] |
| 13 | Stored sponsorship claims re-read | `_recheck_sponsorship` | [ ] |
| 14 | Visa route per accepted job, penalty | `_visa`, `insights/visa.py` | [ ] |
| 15 | Deadlines and reposts | `_timing`, `insights/timing.py` | [ ] |
| 16 | Salaries in euros | `_salaries`, `insights/fx.py` | [ ] |
| 17 | Prune state, checkpoint save | `core/jobs.prune_state`, `StateManager.save` | [ ] |
| 18 | Alert selection (tiers, watch list, mutes, cap), digest, delivery marking | `core/jobs.select_alerts`, `notifications/telegram.py` | [ ] |
| 19 | Weekly summary, follow-ups, deadline reminders | `_weekly_summary`, `_follow_ups`, `_deadline_reminders` | [ ] |
| 20 | Insights: procurement signals, EU tenders, monthly radar | `_insights`, `insights/signals.py`, `insights/radar.py` | [ ] |
| 21 | Health notices | `core/health.py` | [ ] |
| 22 | Final save, run report, raw snapshots, Worker index, retention | `_write_artifacts`, `core/index.py`, `core/report.py` | [ ] |

## 2. Sources

| Source | Backend | Key | Min interval | Status |
|---|---|---|---|---|
| Greenhouse 13 · Lever 4 · Ashby 5 · SmartRecruiters 2 · Workable 3 · Recruitee 3 · Workday 10 (+ discovered boards on rotation) | `scrapers/ats/*` | none | every run | [ ] |
| Employer prospector (watch list, award winners, LMIA geo employers, geo-named sponsors) | `insights/prospects.py` | none | 10 employers a run | [ ] |
| Career sites and sitemaps: Keejob ×5, GEO CAREERS, watched URLs | `scrapers/pages.CareerSitesBackend` | none | every run | [ ] |
| Generic page extraction (JSON-LD, embedded JSON, HTML) | `scrapers/pages.GenericPagesBackend`, `scrapers/generic.py` | none | budgeted | [ ] |
| Board discovery: Common Crawl, SearXNG, DuckDuckGo | `scrapers/search.py` | none | every run | [ ] |
| Remotive, Jobicy, Himalayas, Arbeitnow, RemoteOK | `scrapers/feeds.py` | none | 4–12 h | [ ] |
| RSS: GoGeomatics, GISjobs, Job Bank ×3, Guichet-Emplois ×3, Tunisie Travail ×5 | `scrapers/feeds.RssFeedBackend` | none | every run | [ ] |
| JobSpy: Indeed, LinkedIn | `scrapers/feeds.JobSpyBackend` | none | 4 h | [ ] |
| Adzuna, Jooble, JSearch | `scrapers/feeds.py` | yes | 4 h | [ ] |
| USAJOBS, ReliefWeb | `scrapers/feeds.py` | yes (not set) | 4–6 h | [ ] |
| Bundesagentur, France Travail | `scrapers/official.py` | none / yes | 6 h / 4 h | [ ] |
| freehire | `scrapers/aggregators.py` | none | 4 h | [ ] |
| Hacker News "Who is hiring?" | `scrapers/community.py` | none | 24 h | [ ] |
| Reference data: sponsor registers UK, CA, NL, IE, DK · World Bank procurement · EU tenders · exchange rates | `insights/` | none | weekly / daily | [ ] |

## 3. Telegram commands (`notifications/commands.py`, `notifications/intents.py`, `cloudflare/worker.js`)

| Group | Commands | Python | Worker (instant) | Status |
|---|---|---|---|---|
| Find | `/jobs` `/top` `/high` `/range` `/search` `/why` `/ai` `/sponsors` `/visa` | yes | yes | [ ] |
| Apply | `/pitch` `/draft` `/prep` `/approach` | yes (AI) | dispatch | [ ] |
| Track | `/applied` `/outcome` `/hide` `/unhide` `/watch` `/unwatch` | yes | yes, except watch | [ ] |
| Tune | `/mute` `/unmute` `/muted` `/threshold` `/locations` `/interns` `/possible` `/pause` `/resume` | yes | yes | [ ] |
| Insight | `/status` `/weekly` `/radar` `/skills` `/signals` `/sources` `/prospects` `/learning` | yes | views for some | [ ] |
| Control | `/run` `/help` `/start` `/id` | yes | `/id`, `/help` | [ ] |
| Plain language (EN/FR, typo tolerant) and the Worker's AI hint | `intents.py`, Worker `aiHint` | yes | read-only only | [ ] |

## 4. Where AI is used today

| Use | Model | Input | Guardrail | Status |
|---|---|---|---|---|
| Second opinion per accepted job (fit, summary, concerns, years, sponsorship, languages, requirements) | Workers AI `llama-3.1-8b-instruct` | title, company, place, 3,500 chars | schema validated and clamped; veto only for Possible with fit ≤ 2 | [ ] |
| `/pitch`, `/prep`, `/approach` | same | known facts + 2,500 chars | drafts only | [ ] |
| Free-text message → command hint | same, in the Worker | 300 chars | must be a known command; alone may only open read-only views | [ ] |

## 5. Infrastructure

| Part | Where | Status |
|---|---|---|
| Scraper workflow (cron 2 h, push, stale-commit guard, keep-alive) | `.github/workflows/scraper.yml` | [ ] |
| Commands workflow (webhook dispatch, private inbox) | `.github/workflows/commands.yml` | [ ] |
| Tests workflow (pytest + node) | `.github/workflows/tests.yml` | [ ] |
| Worker: webhook, instant replies, prefs writes, dashboard, watchdog, email | `cloudflare/worker.js` | [ ] |
| Storage: R2, local, backups, conflicts, retention | `geojobbot/storage/` | [ ] |
| HTTP client: delays, retries, robots, challenge detection, size limits | `geojobbot/utils/http.py`, `utils/robots.py` | [ ] |
| Secrets and logging redaction | `config.py`, `pipeline.SecretRedactingFilter` | [ ] |

---

## Findings

### AI

- **F1 · the model was the weakest and one of the dearest available · [fixed]** `ai/client.py` used `@cf/meta/llama-3.1-8b-instruct`.
  Cloudflare's price list (read 2026-09-20) charges it 25,608 neurons per million input tokens; `gpt-oss-120b` costs 31,818 and
  `gemma-4-26b-a4b-it` 9,091, `qwen3-30b-a3b-fp8` 4,625, inside the same free 10,000 neurons a day. The client now takes an ordered
  chain (`AI_MODELS`, default gpt-oss-120b → gemma-4-26b → the old model), falls back when a model cannot be used, stops on quota
  answers, reads all three response shapes Workers AI models return, gives reasoning models room to think, and records the model on
  every review. Not testable from the development machine (the token lives in GitHub secrets): the last link of the chain is the
  model that works today, so the worst case is unchanged behaviour. `/status` and the run summary show which model answered.
- **F2 · the review read only the first 3,500 characters · [fixed]** Sponsorship and eligibility sentences sit at the end of
  postings (the Enviva one was at character 5,900 of 5,961), so the AI's `sponsorship` reading could not see them. Now 6,000, the
  size the description store keeps.
- **F3 · two facts the regexes miss · [fixed]** The review now also returns `restricted` (citizenship, clearance, existing right to
  work, or no sponsorship) and `deadline`; both are validated. `restricted` feeds the visa check like a refusal; the deadline is used
  when the text rules found none.
- **F4 · the fit score ignored what the user does · [fixed]** The prompt now carries the last six applications and dismissals
  (titles and employers only) as calibration.
- **F5 · `/ask` · [fixed]** Free questions answered from the current matches only, with job codes cited and invented codes flagged.
- **F6 · second chance for near misses · [idea → see Findings below once the core review is in]**
- **F7 · semantic matching with embeddings (`bge-m3`, 1,075 neurons per million tokens) · [idea]** Would catch relevant jobs with
  unrecognisable titles ("Software Engineer, Maps"). Needs vectors stored per job and a threshold tuned on real data: recorded, not built.

### Wording rules: sponsorship and work authorisation (`matching/`)  — highest impact, they decide which jobs the user ever sees

- **F8 · yesterday's negation fix rejected genuine offers · [fixed]** Any "no/not/ne/pas" within 70 characters cancelled an offer AND became a rejection, line breaks were not boundaries ("Omaha, NE\nVisa sponsorship is available" → rejected; "If you do not have the right to work in the UK, visa sponsorship is available" → rejected).
- **F9 · "must be able to work outdoors / independently / under pressure" read as a work-authorisation demand · [fixed]**
- **F10 · any mention of a work permit or work visa was a demand, including "employment visa provided by the company" (Gulf postings) · [fixed]**
- **F11 · "relocation assistance provided" counted as visa sponsorship and overrode an explicit refusal · [fixed]**
- **F12 · "will sponsor your licensure", "can sponsor FAA certification", "you will sponsor GIS initiatives" counted as visa sponsorship · [fixed]**
- **F13 · "Visa Sponsorship Available: No" read as an offer · [fixed]**
- **F14 · citizenship / residency / clearance patterns fired on inclusive or unrelated wording ("EU citizens and non-EU nationals alike", "US citizenship is not required", "pathway to permanent residency", "site clearance surveys", "no security clearance is required") · [fixed]**
- **F15 · refusals that were missed ("can't sponsor", "sponsorship isn't available", "will not be considered", French "titre de séjour valide exigé", Arabic nationals-only) · [fixed]**
- **F16 · the identity check every employer runs ("proof of your right to work in the UK", E-Verify) rejected employers that do sponsor · [fixed]**
- **F17 · a refusal to sponsor rejected work-from-anywhere roles, and they got the visa penalty · [fixed]**

### Location, visa, salary, deadlines, registers, learning (`utils/location.py`, `insights/`)

- **F18 · "City, XX" with an ISO country code parsed as a US state: "Tunis, TN" → Tennessee, "Berlin, DE" → Delaware, "Rabat, MA" → Massachusetts, "Amsterdam, NL" → Newfoundland · [ ]** A Tunisian job then got the US H-1B penalty instead of "home country".
- **F19 · deadline detection took posted dates, start dates and contract ends as the closing date, which hides the job (DEADLINE_PASSED) · [ ]**
- **F20 · ambiguous numeric dates (10/08/2026) read day-first everywhere but the US · [ ]**
- **F21 · a stored deadline was never cleared · [ ]**
- **F22 · France Travail salaries multiplied by 12 ("Annuel de 38000 Euros sur 12 mois" read as monthly) · [ ]**
- **F23 · salary ranges sharing one "k" ("£40-50k" → low 40), stray numbers (bonus %, "x 13", "37-hour week", "401(k)"), "up to", Gulf monthly pay with no unit · [ ]**
- **F24 · sponsor-register variant matching: generic one-word cores ("surveys", "mapping", "data") matched unrelated firms and earned the bonus; legal forms stripped anywhere in the name ("AG Survey" = "Survey Co"); "U.K." a false negative; NL parser did not unescape entities · [ ]**
- **F25 · Francophone Mobility: Québec cities without the province not excluded ("Montréal, Canada"), "Quebec Street, Vancouver" excluded · [ ]**
- **F26 · France–Tunisia route: "Chargé d'études" could never match (apostrophe) · [ ]**
- **F27 · learning punished the jobs the user applies to once hides outnumber applications (no base rate) · [ ]**
- **F28 · the visa penalty could not be lifted by the AI once it had pushed a job under the threshold; regex beat an AI "not offered" · [ ]**
- **F29 · negative title words that are real roles or ordinary words: "Survey Party Chief", "GIS Executive", "Commercial", "(Early Stage Startup)" · [ ]**
- **F30 · Arabic "مساح" matched inside "مساحات" (interior space designer scored as a surveyor) · [ ]**

### Core: fusion, state, pipeline order (`core/`)

- **F31 · a shared application URL was job identity: two France Travail or Bundesagentur offers with the same generic apply page fused into one, and across runs a new offer attached to an old notified record and was never alerted · [ ]**
- **F32 · JSON-LD `identifier`: the PropertyValue `name` ("Acme Surveys") was used as the id, and plain ids were not namespaced by host, so different jobs fused · [ ]**
- **F33 · AI veto evaluated before the bonuses: a vetoed job was resurrected by a register bonus; an alerted High job silently vanished when rescored · [ ]**
- **F34 · reposts swallowed: fuzzy match against state records of any age (`FUZZY_STATE_MATCH_DAYS` was never used) · [ ]**
- **F35 · career-page / sitemap jobs dropped out of lists after 5 days while still open (cached pages are not refetched for 7–30 days) · [ ]**
- **F36 · descriptions not kept for jobs a bonus lifted into an accepted tier · [ ]**
- **F37 · tier counts in the report and the Worker's /status could go negative · [ ]**
- **F38 · stale explanations after the adjustment was gone: `learned` after /learning off, `salary_eur` after the salary disappeared, `watched` after /unwatch · [ ]**
- **F39 · alerts only selected from jobs seen this run: a job deferred by the cap, /pause or a failed send on a slow-rotation source was never alerted · [ ]**
- **F40 · `_recheck_sponsorship` withdrew the claim but left its score effect; skipped jobs observed but not rescored · [ ]**
- **F41 · AI review ignored the time budget and ran before the checkpoint save (a kill loses the run) · [ ]**
- **F42 · a transient read error wiped the description store; the same shape for prefs · [ ]**
- **F43 · eviction order bugs: Bundesagentur `german_refs` and signals `seen` trimmed by sort order / set order, not age; board registration O(n²) · [ ]**
- **F44 · `/pause`, `/mute`, `/hide`, `/applied` sent during a run were ignored by that run's digest · [ ]**

### Sources (`scrapers/`, `insights/prospects.py`)

- **F45 · prospects: `company_hint` was echoed back by Greenhouse/Lever/Ashby, so `company_matches` was always true on those ATSs (a loose slug registered an unrelated company's board) · [ ]**
- **F46 · prospects: a failed probe still stamped the employer as checked for 120 days; UNKNOWN registry boards were skipped; known matching boards were not promoted · [ ]**
- **F47 · Bundesagentur read 50 of 816 results in relevance order; detail budget re-spent on the same non-German postings · [ ]**
- **F48 · Job Bank / Guichet-Emplois feeds returned zero entries and the backend reported SUCCESS; any HTML answer counted as "empty" for every feed · [ ]**
- **F49 · Hacker News apply link taken from truncated anchor text (131 of 480 links end in "...") · [ ]**
- **F50 · freehire re-served Adzuna adverts did not fuse with the direct Adzuna job; its parameter guard covered only `q` · [ ]**
- **F51 · pages served without a charset header decoded as ISO-8859-1: "Ingénieur géomatique" garbled and dropped by the prefilter · [ ]**
- **F52 · robots.txt with a UTF-8 BOM treated as allow-all · [ ]**
- **F53 · Atom: `updated` used as the posting date and marked reliable; rel="self" link taken as the job URL; Jooble `updated` marked reliable · [ ]**
- **F54 · quota-bound backends retried every run after a failure (min interval measured from last success) with 3 HTTP retries each; Adzuna 30 calls a run against 250/day and 25/minute · [ ]**
- **F55 · loops without a time-budget check (RSS, USAJOBS, Common Crawl retries) could starve extraction · [ ]**
- **F56 · ATS detail budget always spent on the same first 30 postings (Esri has 451); Workday read only the first 20 hits per term · [ ]**
- **F57 · France Travail: 31-day window, relevance sort; JSearch `date_posted=month` · [ ]**

### Telegram, Worker, index (`notifications/`, `cloudflare/worker.js`, `core/index.py`)

- **F58 · prefs.json lost updates: Python's `_dirty` flag never reset, so a later read-only command re-saved a stale copy over the Worker's writes; no conditional put on either side · [ ]**
- **F59 · plain-language rules turned questions into writes: the typo corrector rewrote real words ("remote"→"remove": "is a3f9c remote?" hid the job); "does a3f9c offer sponsorship" recorded an offer; "should I apply to a3f9c?" recorded an application; "my resume" un-paused alerts; "I prefer remote jobs" replaced preferred locations; "no more US jobs" muted 37 of 160 matches; "stop sending me US jobs" paused everything; "check lidar jobs now" started a 45-minute run · [ ]**
- **F60 · muting was a substring match ("US" silenced "Industry") · [ ]**
- **F61 · email handler: a newsletter mentioning an employer overwrote an "offer" with "rejected"; an acknowledgement mentioning "interview" recorded an interview; nested MIME bodies never read; no limits · [ ]**
- **F62 · `/outcome [code]` with brackets re-created the application (Python); `/applied` on an existing application reset it (both); edited Telegram messages re-executed writes (Worker) · [ ]**
- **F63 · the index kept stale accepted jobs and cut fresh ones at 900; unknown codes answered "Usage" instead of handing over to Python · [ ]**
- **F64 · long replies cut through the HTML: Python lost the reply entirely (no plain-text retry), the Worker truncated silently · [ ]**
- **F65 · `/threshold 75.5` stored Possible ≥ 5 in the Worker; `/range` parity; a message left in the inbox after a failed dispatch executed days later · [ ]**
- **F66 · dashboard: malformed percent-encoding in the key threw (HTTP 500 reveals the dashboard is on); view anchors not restricted to http(s); `/outcome code constructor` accepted a prototype key · [ ]**
- **F67 · Worker `/why` lacked the deadline/repost/skills line; tie order, signals selection and snapshot sizes differed slightly between the two sides · [ ]**

### Opportunities recorded (not bugs)

- **O1** confirmation with inline Yes/No buttons for writes derived from free text; inline buttons under digests (`callback_query`) so nobody types codes · **O2** "more"/"next" paging instead of truncation · **O3** conversation memory ("why 2", "hide the third one") · **O4** sponsorship-first ordering inside a tier, and a small bonus for a strong visa route · **O5** rescore stored jobs from kept descriptions when rules change (`scorer_version`) · **O6** a bonus and badge for French or Arabic postings · **O7** salary as a structured fact at ingestion · **O8** new verified sources: ReliefWeb RSS (no appname needed), Le Forem open data (Wallonia), AfDB vacancies RSS, AECOM on SmartRecruiters (needs `q=`) · **O9** Greenhouse `?content=true` to read all descriptions in one call.
