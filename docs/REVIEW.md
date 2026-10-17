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
| 1 | Load state from R2 (backups, ETag, refusal to start fresh) | `storage/state.py` `StateManager.load` | [ok] HEAD diagnosis, backups, conflict file, read-back. GET and HEAD are two calls (the ETag could belong to a newer object); harmless while the scraper workflow runs one at a time |
| 2 | Pending Telegram commands, then overlay preferences on settings | `_process_commands`, `core/prefs.py` | [fixed] F44, F58, F76 |
| 3 | Build backends (config + watch list + sponsor data for the prospector) | `build_backends` | [ok] |
| 4 | Discovery phase, parallel | `_run_phase("discovery")` | [fixed] F45, F46, F48, F53, F55 |
| 5 | Extraction phase, parallel | `_run_phase("extraction")` | [fixed] F47, F49–F52, F54, F56, F57 |
| 6 | Raw volume per source recorded | `insights/yields.record_run` | [ok] |
| 7 | Fusion: identity keys, grouping, field authority | `core/fusion.py` | [fixed] F31, F32, F34, F50 |
| 8 | Scoring and state merge, change detection, AI veto carried over | `core/jobs.process_fused`, `matching/` | [fixed] F8–F17, F29, F30, F33, F69–F72, title-only sightings (see Sources notes) |
| 9 | Descriptions of accepted jobs kept | `_keep_descriptions`, `core/descriptions.py` | [fixed] F36, F42 |
| 10 | Sponsor registers: weekly refresh, lookup, bonus | `_sponsors`, `insights/sponsors.py` | [fixed] F24 |
| 11 | Learning nudges | `_learning`, `insights/learning.py` | [fixed] F27, F38 |
| 12 | AI second opinion, veto of Possible | `_ai_review`, `ai/` | [fixed] F1–F6, F28, F33, F41 |
| 13 | Stored sponsorship claims re-read | `_recheck_sponsorship` | [fixed] F40: now `_rescore_stored` (also rescoring when `SCORER_VERSION` changes, O5) |
| 14 | Visa route per accepted job, penalty | `_visa`, `insights/visa.py` | [fixed] F17, F18, F22, F23, F25, F26, F28 |
| 15 | Deadlines and reposts | `_timing`, `insights/timing.py` | [fixed] F19–F21 |
| 16 | Salaries in euros | `_salaries`, `insights/fx.py` | [fixed] F22, F23, F38 |
| 17 | Prune state, checkpoint save | `core/jobs.prune_state`, `StateManager.save` | [ok] |
| 18 | Alert selection (tiers, watch list, mutes, cap), digest, delivery marking | `core/jobs.select_alerts`, `notifications/telegram.py` | [fixed] F39, F44, F60, F64, F73, F74, F79 |
| 19 | Weekly summary, follow-ups, deadline reminders | `_weekly_summary`, `_follow_ups`, `_deadline_reminders` | [ok] reminders use the same muting rule now (F60) |
| 20 | Insights: procurement signals, EU tenders, monthly radar | `_insights`, `insights/signals.py`, `insights/radar.py` | [fixed] F43 |
| 21 | Health notices | `core/health.py` | [ok] |
| 22 | Final save, run report, raw snapshots, Worker index, retention | `_write_artifacts`, `core/index.py`, `core/report.py` | [fixed] F37, F63, F78, F79 |

## 2. Sources

| Source | Backend | Key | Min interval | Status |
|---|---|---|---|---|
| Greenhouse 13 · Lever 4 · Ashby 5 · SmartRecruiters 2 · Workable 3 · Recruitee 3 · Workday 16 · Oracle 2 (+ discovered boards on rotation) | `scrapers/ats/*` | none | every run | [fixed] F56: detail requests go to unread postings; Workday reads every page; F68: Oracle Recruiting Cloud |
| Employer prospector (watch list, award winners, LMIA geo employers, geo-named sponsors) | `insights/prospects.py` | none | 10 employers a run | [fixed] F45, F46 |
| Career sites and sitemaps: Keejob ×5, GEO CAREERS, watched URLs | `scrapers/pages.CareerSitesBackend` | none | every run | [fixed] F35, F51, F52 |
| Generic page extraction (JSON-LD, embedded JSON, HTML) | `scrapers/pages.GenericPagesBackend`, `scrapers/generic.py` | none | budgeted | [fixed] F32; RDFa postings are read now |
| Board discovery: Common Crawl, SearXNG, DuckDuckGo | `scrapers/search.py` | none | every run | [ok] F43 (board counting); Common Crawl loop already checks the clock |
| Remotive, Jobicy, Himalayas, Arbeitnow, RemoteOK | `scrapers/feeds.py` | none | 4–12 h | [ok] |
| RSS: GoGeomatics, GISjobs, GeoRezo, GSA-UK, Job Bank ×3, Guichet-Emplois ×3, Tunisie Travail ×5 | `scrapers/feeds.RssFeedBackend` | none | every run | [fixed] F48: the six Job Bank / Guichet-Emplois feeds are empty for every query (checked live 2026-09-20) and were replaced by the `jobbank` backend; F53, F55 |
| JobSpy: Indeed, LinkedIn | `scrapers/feeds.JobSpyBackend` | none | 4 h | [skip] not reviewed this round: a thin wrapper over a third-party scraper (python-jobspy); what it returns goes through the same title filter, fusion and scoring as everything else |
| Adzuna, Jooble, JSearch | `scrapers/feeds.py` | yes | 4 h | [fixed] F53, F54, F57 |
| USAJOBS, ReliefWeb | `scrapers/feeds.py` | yes (not set) | 4–6 h | [fixed] F55; ReliefWeb RSS without an appname recorded as O8 |
| Bundesagentur, France Travail | `scrapers/official.py` | none / yes | 6 h / 4 h | [fixed] F47, F57; France Travail still unverified live (key only in GitHub) |
| freehire | `scrapers/aggregators.py` | none | 4 h | [fixed] F50 |
| Hacker News "Who is hiring?" | `scrapers/community.py` | none | 24 h | [fixed] F49 |
| Reference data: sponsor registers UK, CA, NL, IE, DK · World Bank procurement · EU tenders · exchange rates | `insights/` | none | weekly / daily | [fixed] F24, F43 |

## 3. Telegram commands (`notifications/commands.py`, `notifications/intents.py`, `cloudflare/worker.js`)

| Group | Commands | Python | Worker (instant) | Status |
|---|---|---|---|---|
| Find | `/jobs` `/top` `/high` `/range` `/search` `/why` `/ai` `/sponsors` `/visa` | yes | yes | [fixed] F63, F64, F67 |
| Apply | `/pitch` `/draft` `/prep` `/approach` | yes (AI) | dispatch | [ok] plus `/ask` (F5) |
| Track | `/applied` `/outcome` `/hide` `/unhide` `/watch` `/unwatch` | yes | yes, except watch | [fixed] F62 |
| Tune | `/mute` `/unmute` `/muted` `/threshold` `/locations` `/interns` `/possible` `/pause` `/resume` | yes | yes | [fixed] F58, F60, F65 |
| Insight | `/status` `/weekly` `/radar` `/skills` `/signals` `/sources` `/prospects` `/learning` | yes | views for some | [ok] |
| Control | `/run` `/help` `/start` `/id` | yes | `/id`, `/help` | [ok] |
| Plain language (EN/FR, typo tolerant) and the Worker's AI hint | `intents.py`, Worker `aiHint` | yes | read-only only | [fixed] F59 |

## 4. Where AI is used today

| Use | Model | Input | Guardrail | Status |
|---|---|---|---|---|
| Second opinion per accepted job (fit, summary, concerns, years, sponsorship, languages, requirements) | Workers AI `llama-3.1-8b-instruct` | title, company, place, 3,500 chars | schema validated and clamped; veto only for Possible with fit ≤ 2 | [fixed] F1–F4, F6: model chain, 6,000 chars, `restricted`, `deadline`, decisions, second chances, clock cap |
| `/pitch`, `/prep`, `/approach` | same | known facts + 2,500 chars | drafts only | [ok] plus `/ask` |
| Free-text message → command hint | same, in the Worker | 300 chars | must be a known command; alone may only open read-only views | [ok] the hint may name `/ask`; alone it still only opens read-only views |

## 5. Infrastructure

| Part | Where | Status |
|---|---|---|
| Scraper workflow (cron 2 h, push, stale-commit guard, keep-alive) | `.github/workflows/scraper.yml` | [ok] new variables passed through (`AI_MODELS`, `AI_SECOND_CHANCES_PER_RUN`…); `AI_TIME_BUDGET_SECONDS` added |
| Commands workflow (webhook dispatch, private inbox) | `.github/workflows/commands.yml` | [fixed] F65: inbox items older than six hours are reported, not run; F77 |
| Tests workflow (pytest + node) | `.github/workflows/tests.yml` | [ok] |
| Worker: webhook, instant replies, prefs writes, dashboard, run filler and watchdog, email | `cloudflare/worker.js` | [fixed] F58, F60–F67, F73–F75, F78, F79 |
| Storage: R2, local, backups, conflicts, retention | `geojobbot/storage/` | [fixed] F76 (conditional put); see step 1 |
| HTTP client: delays, retries, robots, challenge detection, size limits | `geojobbot/utils/http.py`, `utils/robots.py` | [fixed] F51, F52 |
| Secrets and logging redaction | `config.py`, `pipeline.SecretRedactingFilter` | [ok] no secret reaches a log line or an error text in the paths touched this round |

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
- **F6 · second chance for near misses · [fixed]** Jobs rejected on relevance alone (never a negative title, the wrong place, work rights or the AI's own veto) that are a few points short, or carry real geospatial content under a title the rules cannot place ("Network Planner" with QGIS and fibre routes), get one reading (`AI_SECOND_CHANCES_PER_RUN`, default 5). Fit ≥ 7 and no restriction lifts the job to Possible; the verdict is stored, so rescoring re-applies it without asking the model again. `/why` says so.
- **F7 · semantic matching with embeddings (`bge-m3`, 1,075 neurons per million tokens) · [idea, partly answered by F6]** Would catch relevant jobs with
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

- **F18 · "City, XX" with an ISO country code parsed as a US state: "Tunis, TN" → Tennessee, "Berlin, DE" → Delaware, "Rabat, MA" → Massachusetts, "Amsterdam, NL" → Newfoundland · [fixed]** A Tunisian job then got the US H-1B penalty instead of "home country".
- **F19 · deadline detection took posted dates, start dates and contract ends as the closing date, which hides the job (DEADLINE_PASSED) · [fixed]**
- **F20 · ambiguous numeric dates (10/08/2026) read day-first everywhere but the US · [fixed]**
- **F21 · a stored deadline was never cleared · [fixed]**
- **F22 · France Travail salaries multiplied by 12 ("Annuel de 38000 Euros sur 12 mois" read as monthly) · [fixed]**
- **F23 · salary ranges sharing one "k" ("£40-50k" → low 40), stray numbers (bonus %, "x 13", "37-hour week", "401(k)"), "up to", Gulf monthly pay with no unit · [fixed]**
- **F24 · sponsor-register variant matching: generic one-word cores ("surveys", "mapping", "data") matched unrelated firms and earned the bonus; legal forms stripped anywhere in the name ("AG Survey" = "Survey Co"); "U.K." a false negative; NL parser did not unescape entities · [fixed]**
- **F25 · Francophone Mobility: Québec cities without the province not excluded ("Montréal, Canada"), "Quebec Street, Vancouver" excluded · [fixed]**
- **F26 · France–Tunisia route: "Chargé d'études" could never match (apostrophe) · [fixed]**
- **F27 · learning punished the jobs the user applies to once hides outnumber applications (no base rate) · [fixed]**
- **F28 · the visa penalty could not be lifted by the AI once it had pushed a job under the threshold; regex beat an AI "not offered" · [fixed]**
- **F29 · negative title words that are real roles or ordinary words: "Survey Party Chief", "GIS Executive", "Commercial", "(Early Stage Startup)" · [fixed]**
- **F30 · Arabic "مساح" matched inside "مساحات" (interior space designer scored as a surveyor) · [fixed]**

### Core: fusion, state, pipeline order (`core/`)

- **F31 · a shared application URL was job identity: two France Travail or Bundesagentur offers with the same generic apply page fused into one, and across runs a new offer attached to an old notified record and was never alerted · [fixed]**
- **F32 · JSON-LD `identifier`: the PropertyValue `name` ("Acme Surveys") was used as the id, and plain ids were not namespaced by host, so different jobs fused · [fixed]**
- **F33 · AI veto evaluated before the bonuses: a vetoed job was resurrected by a register bonus; an alerted High job silently vanished when rescored · [fixed]**
- **F34 · reposts swallowed: fuzzy match against state records of any age (`FUZZY_STATE_MATCH_DAYS` was never used) · [fixed]**
- **F35 · career-page / sitemap jobs dropped out of lists after 5 days while still open (cached pages are not refetched for 7–30 days) · [fixed]**
- **F36 · descriptions not kept for jobs a bonus lifted into an accepted tier · [fixed]**
- **F37 · tier counts in the report and the Worker's /status could go negative · [fixed]**
- **F38 · stale explanations after the adjustment was gone: `learned` after /learning off, `salary_eur` after the salary disappeared, `watched` after /unwatch · [fixed]**
- **F39 · alerts only selected from jobs seen this run: a job deferred by the cap, /pause or a failed send on a slow-rotation source was never alerted · [fixed]**
- **F40 · `_recheck_sponsorship` withdrew the claim but left its score effect; skipped jobs observed but not rescored · [fixed]**
- **F41 · AI review ignored the time budget and ran before the checkpoint save (a kill loses the run) · [fixed]**
- **F42 · a transient read error wiped the description store; the same shape for prefs · [fixed]**
- **F43 · eviction order bugs: Bundesagentur `german_refs` and signals `seen` trimmed by sort order / set order, not age; board registration O(n²) · [fixed]**
- **F44 · `/pause`, `/mute`, `/hide`, `/applied` sent during a run were ignored by that run's digest · [fixed]**

**How the core batch was fixed (F27–F44).** Identity: ids first, URLs second and only when no source both sides know gave
them different ids (`_conflict`); fuzzy matches reach back 60 days. Run order: score → rescore stored jobs whose rules are
outdated or whose sponsorship claim no longer reads as an offer (`SCORER_VERSION`, `rescore_stored`, place re-read too) →
register bonus → learning (judged against the user's own base rate; forgotten when switched off) → AI reviews (clock cap
`AI_TIME_BUDGET_SECONDS`, the user's recent decisions as calibration, jobs held back by the visa penalty included, near
misses as second chances) → visa → AI veto, once and last → deadlines, salaries → tier counts taken from the state.
Alerts reach back to accepted, never-notified, still-listed jobs found within the alert age, and preferences are re-read
just before the digest. Descriptions follow an edited posting, survive a failed read, and are kept for held-back jobs.

### Sources (`scrapers/`, `insights/prospects.py`)

- **F45 · prospects: `company_hint` was echoed back by Greenhouse/Lever/Ashby, so `company_matches` was always true on those ATSs (a loose slug registered an unrelated company's board) · [fixed]**
- **F46 · prospects: a failed probe still stamped the employer as checked for 120 days; UNKNOWN registry boards were skipped; known matching boards were not promoted · [fixed]**
- **F47 · Bundesagentur read 50 of 816 results in relevance order; detail budget re-spent on the same non-German postings · [fixed]**
- **F48 · Job Bank / Guichet-Emplois feeds returned zero entries and the backend reported SUCCESS; any HTML answer counted as "empty" for every feed · [fixed]**
- **F49 · Hacker News apply link taken from truncated anchor text (131 of 480 links end in "...") · [fixed]**
- **F50 · freehire re-served Adzuna adverts did not fuse with the direct Adzuna job; its parameter guard covered only `q` · [fixed]**
- **F51 · pages served without a charset header decoded as ISO-8859-1: "Ingénieur géomatique" garbled and dropped by the prefilter · [fixed]**
- **F52 · robots.txt with a UTF-8 BOM treated as allow-all · [fixed]**
- **F53 · Atom: `updated` used as the posting date and marked reliable; rel="self" link taken as the job URL; Jooble `updated` marked reliable · [fixed]**
- **F54 · quota-bound backends retried every run after a failure (min interval measured from last success) with 3 HTTP retries each; Adzuna 30 calls a run against 250/day and 25/minute · [fixed]**
- **F55 · loops without a time-budget check (RSS, USAJOBS, Common Crawl retries) could starve extraction · [fixed]**
- **F56 · ATS detail budget always spent on the same first 30 postings (Esri has 451); Workday read only the first 20 hits per term · [fixed]**
  Completed 2026-10-10: the first fix still stopped after two pages, because Workday reports `total` on the offset-0 page
  only (0 after; checked live on trimble: 143, then 0 with 20 rows) and serves a small result set again at every later
  offset. The total is read from page 0, and a page that brings no new path ends the term.
- **F57 · France Travail: 31-day window, relevance sort; JSearch `date_posted=month` · [fixed]**

**How the sources batch was fixed (F45–F57), and one thing the review had not listed.** While checking the detail budgets
a wider defect showed: a known job seen again *without* its text (the budget had gone to other postings) was rescored
from the title alone, which drops a High match to the title-only floor until the text is seen again. Now such a sighting
only says "still open" (`worse_description` in `process_fused`), records keep `text_seen`, and `RunContext.knows_text`
lets Greenhouse, SmartRecruiters, Workday and the Bundesagentur spend detail requests on postings nobody has read (texts
are read again after a week, since postings get edited). Job Bank: verified live that every Atom query returns an empty
feed while the search page lists the jobs (19 for "surveyor"); the new `jobbank` backend reads the search pages of both
language sites (robots.txt asks for a 5 s delay and forbids nothing), and the page extractor learned schema.org RDFa,
which is how those postings carry their text. Prospector: no company hint is passed to the probe (adapters echo it
back), an outage no longer parks an employer for 120 days, boards known only by name are probed, validated ones are
promoted to daily reads. Hacker News links come from the `href`. Adzuna adverts re-served by other aggregators fuse by
advert number. Pages without a charset are read as UTF-8 when they are valid UTF-8; a BOM no longer hides robots rules.
Atom uses `published` and the `alternate` link; Jooble dates are not reliable. Quota-bound APIs back off after failures
and Adzuna stays under 25 calls a minute. France Travail is asked newest-first, JSearch for the last week.

### Telegram, Worker, index (`notifications/`, `cloudflare/worker.js`, `core/index.py`)

- **F58 · prefs.json lost updates: Python's `_dirty` flag never reset, so a later read-only command re-saved a stale copy over the Worker's writes; no conditional put on either side · [fixed]**
- **F59 · plain-language rules turned questions into writes: the typo corrector rewrote real words ("remote"→"remove": "is a3f9c remote?" hid the job); "does a3f9c offer sponsorship" recorded an offer; "should I apply to a3f9c?" recorded an application; "my resume" un-paused alerts; "I prefer remote jobs" replaced preferred locations; "no more US jobs" muted 37 of 160 matches; "stop sending me US jobs" paused everything; "check lidar jobs now" started a 45-minute run · [fixed]**
- **F60 · muting was a substring match ("US" silenced "Industry") · [fixed]**
- **F61 · email handler: a newsletter mentioning an employer overwrote an "offer" with "rejected"; an acknowledgement mentioning "interview" recorded an interview; nested MIME bodies never read; no limits · [fixed]**
- **F62 · `/outcome [code]` with brackets re-created the application (Python); `/applied` on an existing application reset it (both); edited Telegram messages re-executed writes (Worker) · [fixed]**
- **F63 · the index kept stale accepted jobs and cut fresh ones at 900; unknown codes answered "Usage" instead of handing over to Python · [fixed]**
- **F64 · long replies cut through the HTML: Python lost the reply entirely (no plain-text retry), the Worker truncated silently · [fixed]**
- **F65 · `/threshold 75.5` stored Possible ≥ 5 in the Worker; `/range` parity; a message left in the inbox after a failed dispatch executed days later · [fixed]**
- **F66 · dashboard: malformed percent-encoding in the key threw (HTTP 500 reveals the dashboard is on); view anchors not restricted to http(s); `/outcome code constructor` accepted a prototype key · [fixed]**
- **F67 · Worker `/why` lacked the deadline/repost/skills line; tie order, signals selection and snapshot sizes differed slightly between the two sides · [fixed]**

**How the Telegram batch was fixed (F58–F67).** Free text: typo repair opens views but never writes (writes are matched on
the words as typed), and a question never writes ("can you hide a3f9c?" is a request, not a question); "resume", "prefer",
"stop sending me X jobs" and "check X jobs now" mean what they say. Preferences: the Worker writes with an R2 conditional
put and retries once on the fresh copy, refuses to write when the stored file cannot be read, and Python re-reads before
every message, saves right after the command and clears its dirty flag; both sides keep keys they do not know. Muting
matches whole words on both sides. `/applied` twice keeps the recorded outcome; edited messages open views but never
write or start a workflow; codes missing from the index go to Python; long replies are cut between blocks and a refused
markup is sent again as plain text; mail is recorded only when it is clearly about that application, is not bulk mail,
and the step follows from the status on record. `/ask` is wired on both sides.

### Sources, second pass (2026-10-10)

- **F68 · WSP (4,049 postings, survey and GIS roles in Canada, the UK, the Gulf, India, Australia) and Michael Baker were
  invisible: both run Oracle Recruiting Cloud, which had no adapter; Topcon, Airbus, Teledyne (Optech), Bentley, Ordnance
  Survey and Vantor (ex-Maxar) are on Workday but were not configured, and the config called WSP, Maxar and Bentley
  unsupported · [fixed]** `scrapers/ats/oracle.py` reads the Candidate Experience REST API (no login; robots.txt is 404).
  Checked live: `limit` is capped at 200, `hasMore` is always false (paging stops on `TotalJobsCount`), and the API answers
  *any* `siteNumber` with the tenant's default site (`CX_9999` returns WSP's jobs), so the site is checked against its
  public page, which redirects an unknown site to another one; a site redirected away is `INVALID`, an unconfirmed one
  `EMPTY_UNVERIFIED`. Sites up to 600 postings are read whole; WSP is searched by the Workday keywords plus "SIG" (17 list
  calls; one page per term on a rotation board), and the generic titles among its newest 200 postings, which are no
  search hits, need a geospatial title. French postings arrive in French without a language parameter (`Accept-Language:
  fr-CA` only swaps translated titles, same ids). Wired into detection (`oracle:{pod}.{dc}:{Id}`, career-page and Common
  Crawl link discovery: one index page holds 194 Oracle sites), fusion's native ids, config URL parsing
  (`parse_board_url`), the hot-board exemption for single job pages, and the search queries. The six Workday tenants were
  each validated live with `WorkdayAdapter`. Pay ranges stay in the description: the bot has no salary extraction from
  text yet (O7).
  Review of the first version, checked live: 27 of those 194 sites are published under a code, not a number
  (`gallifordtrycareers`, `Alta`, `Ford-Model-e`); their page is served in place and names the number behind it
  (`data-sitenumber="CX_1"`), so comparing the two strings called them `INVALID`, and `siteNumber=Alta` lists the
  tenant's default site (92 postings, not Alta's 25). Existence now follows the redirect, the list API gets the served
  number and the public links keep the code. Detection cut hyphenated codes at the hyphen (`Ford`), missed
  `/jobs/preview/{Id}` (66 Common Crawl URLs) and read requisition ids as digits only, so `AUR_NYF8B` had no native id and
  `22000HXO` became `22000`: ids and codes now run to the end of their path segment (4,062 job URLs of that index page,
  1 without an id: it has none). `og:site_name` loses stray separators ("FAB |" → "FAB").

### Matching taxonomy (`matching/profile.py`)

- **F69 · French géomètre-topographe postings were rejected at 54: their daily vocabulary was unknown · [fixed]** Bornage,
  plans de récolement, implantation, nivellement, cubatures; survey office software (Trimble Business Center, Mensura, 12d);
  survey instruments; buried-network detection (DT-DICT, géoradar, PCRS); hydrography; cloud-native formats (STAC, COG,
  GeoTIFF, Zarr) and OGC services. Each word that also lives elsewhere needs survey company in the same sentence (museum
  "récolement", store "implantation", "Salary TBC", Leica cameras, metrology theodolites, finance "COGS", the Office of
  General Counsel, "SAR imagery" in every ICEYE introduction). Rescored on 1,642 live postings (2026-10-10): 60 score
  changes, 5 tier changes, all Hellowork géomètre postings (three 54 → 57–61, two Possible → High), no false positive.
  `tests/unit/test_taxonomy_golden.py` holds every row; `SCORER_VERSION` 3 rescores stored jobs.
- **F70 · Arabic: vowel marks and hamza forms defeated the domain patterns; the title term "خرائط" could never match
  (fold turns it into "خرايط"); "مساحة" (area) counted as surveying; "أنظمة المعلومات الجغرافية" was missed · [fixed]** The
  term text and the description read by the weak-title check are normalised first; the surveyor is matched with its
  attached article ("المساح", "للمساحين") and "مساحة" only next to a survey word.
- **F71 · weak-title survey evidence was inconsistent with the false-positive rules · [fixed]** Bare Leica, Trimble, Topcon
  and theodolite backed a "Surveyor" / "Mapping" title although they are rejected elsewhere (cameras, eye care, fleet
  software, metrology), while survey software and French survey works did not count. Two older windows (Google Earth
  Engine, Leaflet) crossed sentences; every window now stops at `.`, `;` and line ends.
- **F72 · the new terms leaked into ordinary wording of other trades, each one enough to add a family to a generic title or
  back a weak one · [fixed]** Found by two independent reviews of F69–F71: "Salary TBC, AutoCAD" read as Trimble Business
  Center; "Microsoft Sentinel 3+ years" as the Sentinel-3 satellite; "multi-beam antennas for high data rate links" as
  bathymetry; "détection réseaux" in a SOC (NDR, logs) as DT-DICT work; a warehouse WMS next to Kronos WFS as OGC services;
  "Trimble GPS fleet tracking" as a survey instrument; "chaque classe a 12 élèves" as precision class A; museum "récolement
  du fonds", "implantation de boîtiers GPS" and "implantation … de nos magasins" as survey works. Arabic went the other way:
  "كمساح", "ومساح", "مساحو" and the titles "مدير المساحة", "مراقب مساحة", "مسؤول مساحة" were lost (the last three no longer
  passed the title prefilter), while "مساحه" (area) and "مساحيق" (powders) still made a title geospatial. Each case is a
  golden row; the corpus scores did not move (0 changes against F69–F71, so the 5 tier changes above stand). Bare `gps` and
  `rtk` no longer back a weak title either (survey GPS, RTK GPS and GNSS still do), and `/skills add Trimble Business
  Center` now stores the canonical name the radar counts. Term extraction skips the Arabic patterns for text without Arabic
  letters; scoring is still about 1.4× slower than before F69 (281 term patterns instead of 189).

### Telegram, second pass: loudness and digest buttons (2026-10-10)

- **F73 · Worker `reply()` answered any failure, a 429 included, with an immediate plain resend showing literal `&amp;`, and logged nothing when that failed too; loudness was tied to the markup (every HTML reply rang, the watchdog and "Worker error" were silent); `/hide` of a hidden job wrote prefs again · [fixed]** Now: one resend of the same HTML after `retry_after` (at most 5 s), decoded plain text only on "can't parse entities", a log line for the rest, about 1 s between parts. `telegram()` takes an explicit `silent`: replies to a command and "On it" are quiet; mail outcomes, the watchdog and errors ring. Python digests ring once, on the first part, and only with a High match; Possible-only digests, continuation parts, the weekly summary, the radar and market signals are quiet; deadline reminders ring. `link_preview_options` replaces `disable_web_page_preview` on both sides.
- **F74 · digest buttons, second review: without the `INBOX` binding every Applied/Hide tap failed with a generic toast; Undo with no index asked Python for an `/unapply` it does not have; a stale label ("1🙈" after a typed `/unhide`) answered "Already done" and changed nothing; a keyboard Telegram refuses blocked the digest for good; toasts were cut through an emoji; the index reached the Worker only after the reminders and reports, so a tap right after the ring went the slow way · [fixed]** Now: without the bucket a tap hands the typed command to Python; Undo works from the preferences alone and is never handed over; taps always act on the stored state (an unchanged state is still not written) and relabel from it; a refused keyboard is dropped and the message sent without it; toasts are cut between characters; a run that sends buttons publishes the index before the alerts, and again at the end with the final counts.

### Cadence and data safety (research of 2026-10-10, `architecture_verdict`)

- **F75 · the scraper ran at about 40% of its schedule, and the safety net never fired · [fixed]** Between 2026-09-19 and
  2026-10-10 GitHub ran 100 of 255 two-hourly slots (median gap 5.1 h, max 8.9 h); `commands.yml` at :17 is dropped as
  often, so the minute is not the cause. The last `workflow_dispatch` was on 2026-09-18: the watchdog's Cron Trigger
  was probably never configured, and its alert was a silent message. The Worker cron (`41 * * * *`) is now a filler: it reads the
  index's upload time with `head()` (no parse), and when it is 1.75 h old and no scraper run is queued or in progress it
  re-enables and dispatches `scraper.yml`. A crash before the index is written backs off 1, 2, 4 h and stops after four
  starts (critique 4.4); six hours of silence ring once, giving up rings once more, recovery is reported quietly. A
  dispatch GitHub refuses (expired or under-scoped token) is kept in `state/watchdog.json`: it rings at once with the
  HTTP status, the 6-hour alert names it instead of "the run did not finish", and it does not count toward the four
  starts, so the filler resumes by itself once the token is replaced (review of the branch).
- **F76 · Python wrote `prefs.json` blind · [fixed]** F58 made the Worker's writes conditional, not Python's: a `/applied`
  tap or an interview recorded from mail landing between Python's read and its PUT was erased. `get_tagged` returns the
  ETag, `put_bytes(if_match=)` sends `IfMatch` (boto3 >= 1.35.69, checked with a botocore Stubber; 1.34 rejects the
  parameter), a 412 is `ConcurrentModificationError`, and the command runs once more on the fresh copy before any reply
  is sent. `selftest-r2` checks the precondition on the real bucket (a refused right ETag is a FAIL line, and the test
  object is still deleted). The very first write stays unconditional (R2 does not document `If-None-Match: *` on
  PutObject). The interview sheet `/outcome code interview` asks for is drafted after the save, so a retry repeats
  only the prefs change, never the Workers AI call.
- **F77 · a skipped scheduled `commands.yml` run could replace a pending inbox dispatch · [fixed]** The default
  `queue: single` keeps one pending run per group; `queue: max` keeps up to 100 (inferred from the docs, not reproduced).
- **F78 · the Worker's index was pretty-printed, parsed twice on the shortcut path, and never measured · [fixed]**
  `state/index.json` is written without whitespace (about 40% smaller), the shortcut path hands the parsed index to
  `answerFast`, and the run summary and `runs/latest.json` give the gzipped state and index sizes, warning above 8 MB
  and 1 MB. The index is written in its own step: a failed index write no longer takes the run report with it, and
  the summary says so.

### Integration of the 2026-10-10 branches (F68–F78)

- **F79 · the four branches were built apart, and three of their meeting points were wrong once merged · [fixed]** The
  digest buttons publish the index before the alerts, outside the step F78 guards: a bug in `build_index` stopped the
  run before any alert went out (it is now logged there and raised by the final write, after the alerts and the run
  report), and that early copy was pretty-printed (it goes through the same compact writer now). F73 made `telegram()`
  ring unless told `silent`, while the filler's two recovery notes relied on the old rule that plain text is quiet:
  they pass `silent` now, and `ring()` says `false` explicitly. Checked and left alone: Oracle needs nothing like the
  Workday paging fix of F56 (live, 2026-10-10: `TotalJobsCount` is the same on every page, 4,049 at offsets 0 and 200
  for WSP, 283 for "GIS" on both pages, and a page past the end is empty); `SCORER_VERSION` 3 is only referenced by
  name in the other branches' tests; button taps handed to Python run through the conditional prefs write of F76.

### Final review of the integrated branch (2026-10-10)

- **F80 · Oracle jobs carried a tenant-less key · [fixed]** The adapter gave the bare requisition number as the source id;
  fusion turns an id without a colon into "oracle:{Id}", so two tenants posting requisition 2544 would have fused into one
  job. The source id is now the full `oracle:{pod}.{dc}:{Id}`.
- **F81 · WSP's detail budget went to substation engineers · [fixed]** "GIS" also finds gas-insulated-switchgear jobs, whose
  generic titles came first in the site's order and used up the 30 detail requests of a run. Candidates are now read in
  title order: direct geospatial roles first, generic titles last.
- **F82 · GeoRezo: apprenticeships alerted as High, and no place on any of its jobs · [fixed]** Its titles carry tags
  ("[Altern]", "[VSC]", "[THESE]", post-doctorates) that the internship filter did not know, and end with the town and
  département ("- Angers (49)"), which the bot ignored: every alert said "Location not specified". The tags join the
  internship titles; a feed can set `country` and `place_in_title` to read the town (GeoRezo does).
- **Left as they are:** the slow Python replies still ring while the Worker's instant ones are silent (a reply that took a
  minute is worth a sound); sixteen Workday boards take several minutes a run (about 70 s each in the dry run, mostly
  detail requests that `knows_text` saves on later runs): watch `skipped_for_time` in the run summary.

### Owner's report of 2026-10-17: "jobs that require sponsorship slip in; a 97 with 6 of my 13 skills"

- **F83 · the skills part of the score counted every skill a posting named, not the candidate's · [fixed]** The 25 points
  were reached by the posting's own tool list, so with title 40, domain 20 and tasks 10 a GIS-heavy posting reached the 90s
  whatever the candidate knew; the skills list (`MY_SKILLS`, `/skills`) only fed the "you have / not on your list" line.
  Now `skill_points`: the candidate's skills, scaled by the share of the posting's skills they cover, a missing required
  skill weighing 1.5 and a "nice to have" 0.5; ArcGIS Pro covers ArcGIS and ArcMap. Measured on 1,915 real postings
  (the 2026-10-10 dry run and the research corpus): 28 of 73 High become Possible, 13 Possible drop out, none move up;
  "Geospatial Data Engineer" (2 of 9 skills) 83 to 60, "Senior GIS Data Analyst" (6 of 7) stays 92. `/skills` now reaches
  the scorer (`apply_prefs`), digests show "🧰 6/13 skills", `SCORER_VERSION` 4 rescores stored jobs.
- **F84 · three demand wordings the rules missed · [fixed]** Found in accepted jobs of the 2026-10-10 dry run: "Employment
  is limited to candidates authorized to work in the United States" (Ascent Geomatics, twice), a requirement bullet
  "Eligible to work in Germany" (Orcrist, High 83), "requires a Public Trust Clearance" (a US DHS contractor, High 83).
  New patterns, the bullet one anchored to line starts so that "we help you become eligible to work in Germany" stays
  neutral; seven new demand rows and four neutral rows in the golden table.
- **F85 · the AI review's "restricted" only cost 12 points · [fixed]** A High match at 85 stayed High at 73 although the
  model had read "citizens only" or "no sponsorship" in the text. It now rejects (`AI_WORK_RESTRICTED`) unless the posting
  or the model finds a sponsorship offer, the job is in a home country or open worldwide, or the user was already alerted.
- **F86 · years of experience were never read · [fixed]** A live dry run of 2026-10-17 checked against the owner's CV
  (about 3 years) put "10+ years" jobs at High 89 (Parsons, Saudi Arabia), 86 (SYSTRA, WSP Senior Consultant 8-12 years)
  and 75 (WSP, 15+ years). `required_years` reads the smallest number of years a posting asks for, only in sentences
  about experience (16 real sentences in the golden table, including "an average of 15 years of expertise", "CDD de 3 ans"
  and "depuis 13 ans", which do not count); 2-4 years beyond `MY_EXPERIENCE_YEARS` cost 5-15 points, 5 or more reject
  (`EXPERIENCE_GAP`). On that run 10 jobs asking 8-15 years were rejected, and 5-year asks lost 5 points.
- **F87 · the candidate's current work was not a scoring term · [fixed]** Point-cloud registration, scan-to-BIM and 3D
  modelling from scans (with the French recalage / modélisation 3D) are now a responsibility term of the LiDAR family.

### Opportunities recorded (not bugs)

- **O1 · inline Yes/No buttons for writes derived from free text, and buttons under digests (`callback_query`) · [partly done]**
  Digests carry a number per entry (8 per row, none above 90 buttons, on the last part of a split message, only with
  `TELEGRAM_WEBHOOK=true`); a tap opens one action row: ✅ Applied, 🙈 Hide (every posting of a grouped entry), ❓ Why,
  ✖, then ↩️ Undo applied (only a bare "applied" with no later history) or ↩️ Unhide. `callback_data` holds only the action
  and the codes; the Worker answers the owner's taps alone (owner and chat both checked), answers each exactly once,
  redraws the keyboard from the one the tap came with (opening and closing never read R2), retries a lost race once,
  treats "message is not modified" as done, and hands codes missing from the index to Python as the tapped command under
  the update's own id. Setups registered with `allowed_updates=["message"]` must register again (`cloudflare/README.md`
  step 4). `python -m geojobbot set-commands` publishes the "/" menu (12 commands, EN/FR/AR, scoped to the bot's chat).
  Still open: Yes/No buttons for free-text writes, and buttons on `/jobs` replies (with O2 paging).
- **O2 · "more" / "next" paging · [idea]** Long replies are no longer lost or cut through a tag (F64); paging on demand is still open.
- **O3 · conversation memory ("why 2", "hide the third one") · [idea]**
- **O4 · sponsorship-first ordering inside a tier, a small bonus for a strong visa route · [idea]**
- **O5 · rescore stored jobs when rules change · [done]** `SCORER_VERSION`, `rescore_stored`, place re-read.
- **O6 · a bonus and badge for French or Arabic postings · [idea]**
- **O7 · salary as a structured fact at ingestion · [idea]**
- **O8 · new sources · [partly done]** Job Bank search pages (done). WSP and Michael Baker on Oracle Recruiting Cloud
  (done, F68). GeoRezo (French geomatics jobs, full text, 50 entries) and GSA-UK (10 entries, titles filtered) RSS feeds
  (done 2026-10-10, robots.txt allows both). Still open, each verified to exist: ReliefWeb RSS (no appname
  needed), Le Forem open data (Wallonia), AfDB vacancies RSS, AECOM on SmartRecruiters (needs `q=`).
- **O9 · Greenhouse `?content=true` · [skip]** `knows_text` makes detail requests proportional to *new* postings, for every ATS, so
  the one-call variant is no longer needed.
- **O10 · semantic matching with embeddings (F7) · [idea]** Second chances (F6) catch odd titles whose text carries known terms;
  embeddings would catch the rest ("Software Engineer, Maps"). Needs vectors per job and a threshold tuned on real data.

## State of the review

Every row above has a status. 87 findings: 86 fixed, 1 recorded as an idea (F7). Tests went from 280 to 829 (Python) and from 17
to 40 (Worker). Not verifiable from the development machine, and therefore to watch in the next runs: the AI model chain
(the token lives in GitHub secrets; the last model of the chain is the one that worked before), France Travail live answers,
and the first run after deployment, which rescores stored matches (`rescored_from_text` in the run summary) and may carry
over alerts that were held back earlier (`carried_over`).
