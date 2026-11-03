# Project structure and what to change

Written on 2026-11-03, after the report "only one or two jobs per digest". It maps every part of the bot, says what was measured, and gives each part a verdict: **keep**, **improve**, **change** or **replace**. The bug-by-bug record is in `docs/REVIEW.md` (F88 to F101 for this round).

## Summary

The digests were small for three reasons. None of them is a broken source, as far as could be checked: sources that need an API key can only be seen from production.

1. Only High matches rang. Possible matches were switched off in the workflow.
2. Most High matches that did ring (21 of 32) were US jobs behind a visa lottery, so the useful share was under one a day.
3. France's public job postings never reached the bot through a keyless source, and they are the largest supply for this profile.

After this round, the reachable jobs that alert went from about 1.8 a day to about 9 to 14 a day, measured on one live run. Most of the gain is EURES. If France Travail already works in production, part of that gain overlaps with it (see [What these numbers do not cover](#what-these-numbers-do-not-cover)).

| Per day, by posting date | Before | After scoring and age fixes | After new sources too |
|---|---|---|---|
| High, reachable | 0.7 | 1.0 | 2.9 |
| Possible, reachable | 1.1 (no alert) | 1.9 | 6.0 |
| High, hard visa route | 1.1 (alerted) | 1.4 (held back) | 1.0 (held back) |
| Possible, hard visa route | 1.1 (no alert) | 1.5 (held back) | 1.1 (held back) |
| **Alerts per day** | **1.8** | **2.9** | **8.9** |

The columns come from separate live runs on the same day, so small differences between them are noise. The last column counts 14 days of postings. Counting only the last 7 days gives 13.6 a day, which is where the "9 to 14" comes from. With about 5 runs a day that averages 2 or 3 jobs per digest, so the daily figure is the one to judge by. The first days will be larger, because the backlog of open jobs comes out at up to 20 per run, best first.

## The pipeline

```
 GitHub Actions cron (every 2 h) ─┐
 Worker filler cron (:41, hourly) ┴─> scraper run (budget 40 min)
                                        │
 discovery   career pages, sitemaps, search, Common Crawl
                                        │  boards and job pages found
 extraction  ATS APIs · public services · aggregators · feeds · JobSpy   (6 at a time)
                                        │  1,982 raw observations
 fusion      one record per posting                                      1,846 unique
                                        │
 scoring     title 40 · skills 25 · domain 20 · tasks 10 · location 5
             years asked, temporary work, work-rights wording            108 High, 210 Possible
                                        │
 adjustments sponsor registers · learning · AI review · visa route · deadlines · salaries
                                        │
 alerts      not stale · not a hard route · one per posting · 20 per run
                                        │
 storage     R2: state, prefs, index, descriptions, run reports, raw snapshots
                                        │
 Telegram    digest with buttons; the Cloudflare Worker answers commands from the index
```

The counts are from the dry run of 2026-11-03 (empty state, no API keys, Common Crawl and search switched off).

## Part by part

### 1. Scheduling: keep

`.github/workflows/scraper.yml`, Worker `scheduled()` in `cloudflare/worker.js`.

- GitHub dropped 61% of the two-hourly slots in the weeks measured, so the Worker starts a run when the index is 1¾ hours old.
- Median gap between runs is 4.7 hours (90th percentile 6.9). Runs take about 45 minutes in production.
- A dispatched run and a scheduled run can collide. One waited 77 minutes in the queue.

**Could improve:** if 2 or 3 jobs per digest still feels thin, bundle alerts into two digests a day instead of one per run. No jobs are gained, but each message is fuller. Not done, because you did not ask for fewer messages.

### 2. Sources: change (done in part)

`geojobbot/scrapers/`, `config/sources.toml`. See the [source table](#source-table).

- **Added:** EURES, Workable-wide search, and the boards of AtkinsRéalis, Stantec, Acciona, Ferrovial, Eiffage, Surbana Jurong and Saildrone.
- **Removed:** Capella Space (404), three Tunisie Travail searches, Job Bank's "GIS" term.
- **Fixed:** Workday spent its limited detail requests on titles like "Engineer" ahead of the geospatial ones. All boards now read geospatial titles first.

**Replace candidate:** Common Crawl took 227 seconds, hit 504 errors and produced nothing accepted. Check `/sources` over a month and switch it off if that holds (`COMMONCRAWL_ENABLED=false`).

**Replace candidate:** LinkedIn through JobSpy is the best non-French source, and it can stop working at any time. LinkedIn alert emails would be a sturdier way to get the same jobs ([decision 1](#decisions-for-the-owner)).

### 3. Fusion: keep

`geojobbot/core/fusion.py`. Merged 1,982 observations into 1,846 jobs, and 136 duplicates in this run is a plausible rate. Postings of one title in several towns are separate records by design, so that `/jobs` shows each town. Alerts now collapse them to one.

### 4. Scoring: keep, corrected

`geojobbot/matching/`. Deterministic and testable, and that is its strength. Changes this round:

| Rule | Before | Now |
|---|---|---|
| Posting names no tools | 0 of 25 skill points | 6 of 25 |
| Posting names only general software | no penalty | counted as skills missing, title part capped |
| "Fort de 20 ans d'expérience" | job rejected for asking 20 years | ignored, it describes the employer |
| "Entre 3 et 6 ans" | read as 6 | read as 3 |
| "5 années d'expérience" | not read | read as 5 |
| Temp-agency mission, season, contract of 6 months or less | no effect | 15 points off |

All 137 postings read as asking 6 or more years in the previous full run were checked one by one, and none was misread (rejection starts at 8 for someone with 3). The employer's-record misreading only appeared with the French text that EURES brought in.

**Known limit:** stored text is cut at 6,000 characters. A job whose requirements sit past the cut cannot be rescored correctly, so cut text is not rescored at all.

### 5. Adjustments: keep

`geojobbot/insights/`. Five sponsor registers matched 202 of this run's jobs. The visa routes live in `config/visa_paths.toml` and need a yearly refresh.

**Improve next:** a route marked "open" says a route exists, not that the employer will use it. Many French employers EURES brings have no sponsorship history. Now that volume is higher, the order inside a digest matters, so sponsor-register employers should come first inside a tier (O4 in `docs/REVIEW.md`).

### 6. AI review: keep

`geojobbot/ai/`. Up to 40 reviews per run, best matches first, and 7 minutes at most. Not testable from the development machine, because the token is a GitHub secret.

**Watch:** more accepted jobs means more reviews deferred to later runs. The run report shows this as "deferred to the next run". All five prompts now treat posting text as untrusted data.

### 7. Alerts: changed

`select_alerts` in `geojobbot/core/jobs.py`.

| Rule | Before | Now | To reverse |
|---|---|---|---|
| Possible matches | no alert | alert | `/possible off` |
| Hard or blocked visa route | alert | held back, still in `/jobs`, watched employers still alert | `ALERT_HARD_ROUTES=true` |
| Age limit on employers' own systems and public services | 15 days | 60 days | `MAX_LIVE_JOB_AGE_HOURS=360` |
| One title in several towns of a country | one alert each | one alert, best score | none |

Reachable High / Possible by age limit, measured before the new sources: 15 days 11/16, 30 days 16/19, 60 days 17/28.

### 8. Telegram and the Worker: keep

`geojobbot/notifications/`, `cloudflare/worker.js`. Replies come from `state/index.json` in under a second. The index holds at most 900 jobs, so higher volume cannot grow it past that. This run's index was 796 KB.

**Limitation:** the Worker is deployed by pasting, so changes do not arrive with a push. This round needs one paste.

### 9. Storage: keep

`geojobbot/storage/`. R2 holds state (400 KB gzipped in this run), preferences, the index, kept descriptions, run reports and raw snapshots. Nothing here limits the bot today.

### 10. Visibility: improve

Production logs cannot be read from the development machine, so everything about keyed sources in production depends on `/status` and `/sources`. DuckDuckGo's bot challenge used to look like "no results" and now reports `BLOCKED`.

## Source table

Dry run of 2026-11-03, from the development machine. *Reachable* means accepted and not on a hard visa route. *Fresh* means posted in the last 14 days. A job seen by two sources counts for both.

| Source | Seconds | Seen | Accepted | Reachable | Fresh | Verdict |
|---|---|---|---|---|---|---|
| `eures` (new) | 162 | 120 | 99 | 99 | 61 | keep: the largest supply, mostly France |
| `freehire` | 5 | 175 | 27 | 26 | 24 | keep |
| `rss_feeds` | 12 | 57 | 46 | 41 | 16 | keep; `gisjobs` is thin, 7 items |
| `jobspy` LinkedIn | 59 | 36 | 27 | 13 | 13 | keep for now, see decision 1 |
| `jobbank` | 13 | 26 | 18 | 18 | 5 | keep |
| `workday` | 991 | 249 | 23 | 21 | 4 | keep; by far the slowest, 21 boards |
| `generic_pages` | 128 | 115 | 19 | 9 | 6 | keep; reads GEO CAREERS postings |
| `workable_search` (new) | 6 | 35 | 17 | 11 | 3 | keep |
| `himalayas` | 10 | 72 | 19 | 7 | 2 | keep |
| `greenhouse` | 129 | 595 | 12 | 6 | 2 | keep; most of these firms hire engineers, not GIS staff |
| `oracle` | 186 | 72 | 25 | 20 | 0 | keep; older but still open jobs |
| `ashby` | 3 | 232 | 2 | 2 | 1 | keep, cheap |
| `smartrecruiters` | 12 | 16 | 1 | 1 | 1 | your call, decision 4 |
| `jobspy` Indeed | with LinkedIn | 17 | 4 | 0 | 0 | your call, decision 5 |
| `lever`, `recruitee`, `workable` boards | 11 | 83 | 0 | 0 | 0 | improve: review which boards are listed |
| `remotive`, `jobicy`, `hn_hiring` | 7 | 7 | 0 | 0 | 0 | cheap; retire if a month of `/sources` agrees |
| `bundesagentur`, `arbeitnow`, `remoteok` | 35 | 0 | 0 | 0 | 0 | same; Bundesagentur's postings are in German, which the bot skips |
| `commoncrawl` | 227 | 0 | 0 | 0 | 0 | replace candidate (earlier run) |
| `search_duckduckgo` | 48 | 0 | 0 | 0 | 0 | blocked from this machine (earlier run); `/sources` will say about Actions |
| `adzuna`, `jooble`, `jsearch`, `francetravail`, `usajobs` | | | | | | not measured: keys are GitHub secrets |

### What these numbers do not cover

- **API keys.** Adzuna, Jooble, JSearch and France Travail run in production but not here. `/sources` is the only view of them.
- **EURES and France Travail overlap.** EURES carries France Travail's postings. If that key works in production, part of EURES's French supply is already reaching you, and the gain will be smaller than the table suggests. Digests of one or two jobs make that seem unlikely, but only `/sources` can settle it.
- **One run.** This is one snapshot from one machine. JobSpy locally searches "Remote" only.
- **"Reachable" is the rules' reading.** A route being open does not make an employer willing to sponsor.

## The two repositories

**`ATAboukhadra/job_finder`: ignore.** It has no licence, no list of company slugs and no source the bot lacks.

**`career-ops-hq/career-ops` (MIT, Node): borrow ideas, no code.**

| Idea | State |
|---|---|
| Guard against instructions inside postings | done this round |
| Read LinkedIn alert emails | your decision (decision 1), probably the largest gain left |
| `coverage-check`: why did the bot miss a posting I found by hand? | recorded as O12 |
| Closure phrases as a check that a job is still open | recorded as O13 |
| Degree counted as years of experience, repost rules | not needed yet |

Not to copy: spoofed browser identities, APIs forbidden by robots.txt, borrowed Algolia and VDAB keys, Playwright through Cloudflare.

**Datasets worth knowing:** `stapply-ai/ats-scrapers` (MIT, company lists per ATS) and `Feashliaa/job-board-aggregator` (MIT). Do not import them wholesale: 60 random Greenhouse companies from such a list had no geospatial title at all. Wide searches such as EURES and Workable find more than longer board lists do.

## APIs checked

**Added:** EURES, Workable-wide search, Stantec's and AtkinsRéalis's boards.

**Worth a look later:** jobdataapi (3 to 5 jobs a week, free tier limited to 10 requests an hour), Impactpool, WSP France's Teamtailor feed, Arcadis on Oracle, JobTech in Sweden.

**Skipped, and why:**

| Reason | Sources |
|---|---|
| Too few relevant jobs | Job-room (Switzerland), NAV (Norway), UNDP |
| robots.txt forbids it | Tyomarkkinatori, Le Forem, Reddit, Welcome to the Jungle, ReliefWeb's RSS |
| Blocks bots | HiringCafe, Devex, ADB, JobsIreland, Jadarat, VDAB, The Muse, Cadremploi (403, it answered in October) |
| Down when checked | UK Find a Job, ANAPEC, ANEM, IOM |

## Decisions for the owner

1. **LinkedIn alert emails (the postponed step 10).** You set LinkedIn job alerts to arrive at a Cloudflare address and the Worker reads them. Recommended: it is probably the largest gain left, and it would rely less on JobSpy. It is untested, and it needs Email Routing on a domain.
2. **ReliefWeb.** Request a free app name and set `RELIEFWEB_APPNAME`. Recommended: UN and NGO GIS jobs, often hired internationally, and the code is ready.
3. **SmartRecruiters-wide search.** Roughly 3 to 5 jobs a week, but its terms bar "automatic means". Recommended: leave it out.
4. **The current SmartRecruiters boards.** The API's `robots.txt` says `Disallow: /`, and the yield here was 1 job. Recommended: remove them.
5. **Indeed through JobSpy.** It works by impersonating the iOS app, and gave no reachable job here. Recommended: `JOBSPY_SITES=linkedin`.
6. **Reed (UK).** A free key, yield unknown. Worth trying if the UK is a real target.
7. **Your skills.** SQL and PostGIS are what postings ask for most. If you know them, send `/skills add SQL PostGIS`: until then they count as missing.
