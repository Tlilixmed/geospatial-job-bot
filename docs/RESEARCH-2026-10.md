# Research round of 2026-10-10: gap analysis and roadmap

Written by a research workflow (62 findings, each checked live by a separate verifier, then reviewed by a critic) before the
implementation of the same day. The roadmap's status after that implementation:

| # | Item | Status |
|---|---|---|
| 1 | Restore the 2-hour cadence (Worker filler cron) | done (F75); needs the Cron Trigger `41 * * * *` in the Cloudflare dashboard |
| 2 | Config-only coverage (six Workday tenants, GeoRezo, GSA-UK) and the Workday paging fix | done (F68, F56) |
| 3 | French survey taxonomy | done (F69-F72), with Wave 2 (Arabic) and Wave 3 (enrichment); `SCORER_VERSION` 3 |
| 4 | Inline buttons on digests, Worker `reply()` fix | done (F73, F74); needs `allowed_updates` with `callback_query` and `TELEGRAM_WEBHOOK=true` |
| 5 | Oracle Recruiting Cloud adapter (WSP, Michael Baker) | done (F68, F80, F81) |
| 6 | Python prefs conditional write | done (F76) |
| 7 | Greenhouse `content=true&pay_transparency=true`, SmartRecruiters `q=` for AECOM | open |
| 8 | Arabic pattern fixes | done (with item 3) |
| 9 | Telegram polish (silent policy, link previews, `set-commands`) | done; expandable quotes and `<tg-time>` not done |
| 10 | Taxonomy second wave | done where the corpus showed no false positives |
| 11 | Generic-board plumbing (sitemap slugs, microdata), then Tanitjobs, Emploitic, Impactpool, Welcome to the Jungle, Esri Canada | open |
| 12 | Cheap hardening (`queue: max`, compact index, size tripwires) | done (F77, F78); the `x-amzn-waf-action` check is open |
| 13 | JobSpy's Indeed path impersonates the Indeed iOS app | open: the owner's decision |
| 14 | Later or optional items | open |

The report as written before the implementation follows.

---

**Scope.** I read the repository at HEAD 95a8257 without changing it: `geojobbot/`, `config/sources.toml`, `cloudflare/worker.js`, `docs/REVIEW.md` (67 findings fixed, O1-O10 open) and the READMEs. Every recommendation below rests on evidence that the research pass verified on 2026-10-10 with live requests or the bot's own code. Where something is only partly confirmed, or rests on a source's word, I say so. The goal I ranked against is yours: find geospatial jobs you can actually get, with visa sponsorship, in French, English and Arabic markets.

**Setup facts used throughout:**
- The scraper cron is `0 */2 * * *`, so every 2 hours. `README.md:298` still says "4-hourly", which is out of date.
- State is one gzipped JSON in R2, written by one Actions run at a time.
- The Worker already writes `prefs.json` with R2 conditional puts.
- The Telegram webhook was registered without `allowed_updates`, so `callback_query` updates already reach the Worker. `cloudflare/README.md:47` documents `["message"]`; that line needs fixing so a later re-registration does not turn buttons off.

---

## 1. Phase 1: what was verified

### 1.1 Sources and ATS platforms

| Finding | Status | Key evidence |
|---|---|---|
| **WSP and Michael Baker are on Oracle Recruiting Cloud, with a public REST API and no login** | Confirmed | WSP site CX_2001: 4,049 jobs, 283 for "GIS" (Vancouver, Noida, Dubai, Newcastle). Michael Baker CX_2: 35 GIS hits. `GET https://emit.fa.ca3.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber=CX_2001,limit=25,offset=0,keyword=%22GIS%22,sortBy=POSTING_DATES_DESC`. `sources.toml:69` wrongly lists WSP as "not on any supported ATS". |
| **Bentley, Ordnance Survey, Topcon, Vantor (ex-Maxar), Airbus and Teledyne/Optech are on Workday, which the bot already supports** | Confirmed | The bot's own `validate-sources` marked all six VALID. Maxar and Bentley are wrongly listed as unsupported. |
| **Workday adapter bug: only 2 of 3 pages are fetched** | Confirmed | Workday returns `total` only on the offset-0 page. `more_ats.py:320` breaks on `offset + WORKDAY_PAGE >= total`, so the effective cap is 40 hits per term, not 60. |
| **Greenhouse `?content=true&pay_transparency=true` returns full text and salary in one call** | Confirmed | Woolpert: 92 of 115 jobs carry pay ranges. Esri: 438 of 460. Planet: 108 of 119. On the list endpoint `pay_transparency` is undocumented but works. Esri is 5.6 MB decompressed and about 349 KB gzipped. REVIEW O9 skipped `content=true` for request-count reasons; salary is a new reason to revisit it. |
| **SmartRecruiters `q=` finds AECOM's GIS roles** | Partly | AECOM2 has 5,515 postings. `q=GIS` takes 2 calls and returns 164, 11 of them GIS-titled (Bucharest, Singapore, Perth, Bengaluru, US). It brings nothing for the two configured boards. `q` matches full text, so the title prefilter stays mandatory. |
| **iCIMS classic portals (Dewberry, KCI) work today as `[[career_sites]]`** | Partly | Each has a sitemap with lastmod and JSON-LD job pages. The `pages.py` slug prefilter reads the last path segment, which is always `job`, so every URL scores 0 relevance. EagleView and Merrick are iCIMS Jibe (a public JSON API) instead. NV5's portal disallows crawling in robots.txt. All four are US firms. |
| **SuccessFactors (Esri Canada) sitemap works, but the French pages extract 0 jobs** | Partly | The pages use `itemprop` microdata. `parse_rdfa_posting` reads only `[property]`, and `JOB_SIGNAL_RE` is English-only. |
| **Teamtailor, Pinpoint, Rippling and BambooHR all have public JSON or RSS** | Confirmed | Geospatial yield is low: Metria, Geodata and Norkart have 2-3 items, mostly summer or open-application posts. Pinpoint's Trilon has about 30 US survey roles. Terra Remote Sensing has 1 job from 2025. Chloris and Satellogic are on BambooHR. |
| **Conditional GET (ETag/304) works on Greenhouse, Lever, Ashby and SmartRecruiters** | Confirmed | The bot sends none, but `knows_text` already removes most of the repeated detail cost. |
| **The honest User-Agent gets JSON-LD from iCIMS; a browser UA gets an iframe shell** | Confirmed | The AWS WAF 405 the researcher reported was not reproduced on 6 tenants. Checking the `x-amzn-waf-action` header is cheap hardening. |
| **JobSpy's Indeed path uses a hard-coded Indeed mobile-app API key and an iOS app UA** | Confirmed | `jobspy/indeed/constant.py:98`. It is on by default (`config.py:126-127`) and marked unreviewed in `REVIEW.md:51`. |

### 1.2 Job boards

| Board | Status | Verdict |
|---|---|---|
| **GeoRezo** `https://georezo.net/extern.php?fid=10&show=50` | Confirmed | Every post is a geomatics, SIG or topography job: about 50 in 6 weeks, full text, works with the existing `RssFeedBackend` and no code. The best French source found. |
| **GSA-UK** `https://www.gsa-uk.org.uk/job-opportunities/feed/` | Partly | UK survey roles, about 7-8 a week, full text. Some posts are sales or project-manager roles, and there are near-duplicates. |
| **Cadremploi** `https://www.cadremploi.fr/emploi/liste_offres?query=sig` | Confirmed (refutes the "blocked" claim) | 28 results, about 10-15 real SIG jobs, JSON-LD on detail pages. Do not use `/rss/`, which robots.txt disallows. |
| **Tanitjobs** (Tunisia) | Confirmed | Sitemap plus JSON-LD. 0 genuine geospatial postings today; the one slug hit was automotive "géomètre". |
| **Emploitic** (Algeria) | Confirmed | 4 topographe postings out of 4,377. |
| **Impactpool** | Partly | 90 GIS results; one URL returns only 40, so paging is needed. UNITAR, UNOPS, ICRISAT, WFP. No JSON-LD. |
| **UN Careers API** | Partly | A single keyless POST returns all 462 open jobs. Today's 3 geospatial hits are 2 consultancies and 1 internship, with no UN visa. |
| **Welcome to the Jungle** | Partly | About 90k sitemap URLs and 4-6 geomatics jobs per 10k. Needs sitemap-cap changes. |
| **AfDB RSS** | Partly | Works only with the bot's UA; Cloudflare challenges browser UAs. No geospatial items today. |
| **ReKrute** | Refuted for honest use | Returns 403 to the bot UA. The only UA it accepts is the one its robots.txt disallows. Do not add it. |
| **GIS Jobs Clearinghouse** | Confirmed | US only, about 6 a month. Low value for you. |
| **Bayt, GulfTalent, Naukrigulf, Climatebase, Emploi.ma, APEC, UNTalent, UNjobs** | Confirmed dead ends | Blocked by Cloudflare or DataDome, or disallowed by robots.txt. Gulf coverage has to come from employer ATS boards. |

### 1.3 Matching taxonomy

These results come from a corpus of 860 live postings that pass the title filter, rescored with the bot's own scorer. 43 of them are French.

| Term group | Matched | Tier flips (default config) | Verdict |
|---|---|---|---|
| French topographic works (bornage, récolement, implantation, nivellement, cubatures, profils) | 14 (38% of French postings) | **3** (54 to 57) | Add |
| Survey office software (Mensura, TBC long form, 12d, Leica Infinity, Carlson, Magnet) | 7 | **2** (54 to 58) | Add |
| Survey instruments (brand plus noun, RTK, guarded theodolite) | 13 | 0 | Add with a guard; small gain |
| ROLE_NOUNS + technologist, technologue, hydrographer, photogrammetrist | 1 title | 0 (1 without the work-authorisation filter) | Add; costs nothing |
| Network detection (DT-DICT, PCRS, géoradar, classe A) | 15 | 0 | Add; France Travail lists 20+ live titles of this kind that score generic or none |
| Cloud-native formats (STAC, COG, GeoTIFF, Zarr) | 19 | 0 (1 without work-auth) | Add as enrichment |
| OGC services, vector tiles, MapLibre, deck.gl | 5 + 3 | 0 | Add |
| Hydrography domain and software | 11 / 8 | 0 (6 reach high without work-auth) | Add; every hit is Woolpert, which refuses sponsorship |
| Remote-sensing missions | 10 | 0 | Add Sentinel, Landsat, PlanetScope, InSAR. **Drop "SAR imagery"**: 6 of 7 hits are ICEYE boilerplate in non-geospatial roles |
| Arabic fixes | 0 (no Arabic postings in the corpus) | n/a | Fix anyway. `خرائط` never matches after `fold()`, and `مساحة` (area) triggers Surveying |
| Reality capture, GeoAI, mining, utility network, elevation models | 0-13 | 0-1 | Low priority. Reality capture only through `scanner 3D` |
| All candidates together | 91 score changes | **5** (all French géomètre postings) | |

### 1.4 Cloudflare, R2 and GitHub Actions

- **Scheduler (the most important finding in this area).** Between 2026-09-19 and 2026-10-10, 155 of 255 two-hour slots had no scraper run (61%). Gaps between runs: median 5.1 h, p90 7.1 h, max 8.9 h. `commands.yml`, which runs at :17, is dropped at the same rate, so moving off minute :00 would not fix it. The likely cause is throttling of new repositories (unconfirmed). The watchdog never fired: the last `workflow_dispatch` was on 2026-09-18, so its Cron Trigger is probably not configured.
- **Prefs race.** Python writes `prefs.json` without a condition. A Worker write, including the inbound-mail handler recording an interview, can be silently overwritten. The window is under a second to a few seconds. R2 PutObject supports If-Match (412 PreconditionFailed), and boto3 has `IfMatch` from botocore 1.35.69.
- **`state.json` save.** It is HEAD-then-PUT, not atomic. A reproduced lost update needs a local run that writes state while an Actions run is saving. Low risk.
- **Index cost.** `state/index.json` is pretty-printed, capped at 900 rows by count with no byte cap, and parsed twice on the free-text path. A realistic 900-row index is about 1.1 MB and takes 3-6 ms to parse, against the 10 ms CPU limit on Workers Free. Marginal, and the real size has not been measured.
- **R2 budget.** About 40 Class A operations per run, roughly 15k a month: 1.5% of the free tier. State is about 210 B per job gzipped, so 0.63 MB at 3k jobs.
- **Rejected alternatives:** D1 (100k rows written per day per account, then every query fails until 00:00 UTC), KV (eventually consistent, no conditional put), Durable Objects, Queues (24 h retention), Actions cache and artifacts (eviction, public exposure), Bloom filters, and combined raw snapshots.
- **`commands.yml` concurrency.** A scheduled, skipped run can replace a pending inbox dispatch (inferred, not tested live). `queue: max` fixes it in one line.

### 1.5 Telegram (Bot API 10.3, 2026-08-24)

- **Callbacks.** `callback_data` is 1-64 bytes. `answerCallbackQuery` is mandatory; without it the client shows a spinner. Its deadline is undocumented. `editMessageReplyMarkup` has no documented age limit for the bot's own messages. `DisabledButton` was added in 10.3. Limits of 8 buttons per row and 100 per keyboard come only from community sources.
- **Callback data is untrusted.** The handler must check `from.id` against the owner and `message.chat.id` against the chat. The repo is public, so the button scheme will be readable by anyone.
- **Worker `reply()` 429 handling.** Reproduced: a 429 triggers an immediate plain-text resend, which leaves literal `&amp;` entities, and when both calls fail the part is lost with no log line.
- **Available upgrades:** `disable_notification` (from 2016), `<blockquote expandable>` (7.4), `link_preview_options` (7.0; the old field is no longer in the docs), `<tg-time>` (9.5), and `setMyCommands` with scope and language (5.3).

---

## 2. Phase 2: gap analysis

### 2.1 Missed opportunities: jobs the bot cannot see

1. **WSP (Canada, UK, Gulf, Australia; EN and FR).** It is one of the largest geospatial and survey employers, with 283 GIS-matching postings today, and the bot sees none of them. This is the most valuable single missing employer. It needs a new adapter.
2. **Workday tenants that are one config line away.** Topcon, Airbus (Barcelona geospatial roles), Teledyne Optech (Canada), Bentley, Ordnance Survey and Vantor. Vantor's roles mostly need US clearance and some Airbus Defence roles need EU nationality, so expect noise. Separately, the pagination bug costs a third of the hits on every Workday tenant already configured.
3. **French geomatics boards.** GeoRezo is about 40 non-internship geomatics jobs a month, with full text and zero code. Cadremploi adds about a dozen SIG jobs per search. These are your highest-yield French gaps.
4. **French survey vocabulary.** Real géomètre-topographe postings stall at 51-54 because the bot does not know Mensura, TBC, implantation, récolement or bornage as scoring evidence. Measured: 5 French postings change tier with the proposed terms.
5. **Titles that never pass the title gate.** "Aide Opérateur détection de réseaux" and "Chargé de Détection réseaux" are classified `none`. "Survey Technologist" is `none` and "Survey Engineer" is generic. The two survey titles do not appear on the configured boards, so their value is unproven.
6. **AECOM worldwide GIS roles.** These include Bucharest, Singapore and Perth. They need SmartRecruiters `q=`, which REVIEW O8 already lists.
7. **Salary.** Greenhouse pay ranges exist for most Esri, Woolpert and Planet postings, and the bot never reads them (REVIEW O7).
8. **Arabic.** The patterns have real bugs, but no configured source is Arabic. The Gulf boards are blocked, and Bayt via JobSpy is optional and returns 400 or 403. Fixing the patterns is correctness work, not new coverage.

**A caveat that applies to many additions.** US employers (Woolpert, Esri US, Michael Baker, Dewberry, KCI, Trilon, GJC) usually state that they do not sponsor. The bot correctly rejects those postings (WORK_AUTHORIZATION_REQUIRED). So US-heavy sources add little for you, and the ranking reflects that.

### 2.2 Architectural bottlenecks

1. **Cadence.** The bot runs at about 40% of its intended frequency, and the safety net that should catch this, the watchdog, is not running. This costs more freshness than any other single issue.
2. **Silent prefs loss.** The application history is your job-search log, and the email handler writes to it at arbitrary times. Python's blind write can erase those updates.
3. **Workday paging.** One wrong break condition, explained in 1.1.
4. **Sitemap mode on generic boards.** It assumes the slug is the last path segment, URL-decodes nothing, and caps each file at 5,000 URLs. That blocks iCIMS, Welcome to the Jungle, Tanitjobs and Emploitic from working well.
5. **The Worker's index parse.** It grows with the index and runs twice on free text. Measure it before acting.

### 2.3 UX deficits

1. Acting on a job means typing `/applied 79197`, copying a 5-character code on a phone (REVIEW O1).
2. Every digest part rings, including Possible-only digests and summaries. Meanwhile the watchdog and Worker-error alerts are silent, which is backwards.
3. Worker replies can lose parts under a 429 and can show literal `&amp;`.
4. AI notes, visa route and "why" lines are always expanded, so each entry runs 4-6 lines.
5. "closes in 3 days" is static text and goes stale in old digests.
6. With 37 commands and no published command menu, they are hard to discover.

---

## 3. Prioritised roadmap

Impact is measured against your goal: geospatial jobs you can get, with sponsorship, in FR, EN and AR. Effort: S is under half a day, M is 1-3 days, L is more.

| # | Item | Impact | Effort | Why now |
|---|---|---|---|---|
| 1 | **Restore the 2-hour cadence.** Turn the Worker watchdog into an hourly "filler" cron at :41: dispatch `scraper.yml` when the `state/index.json` upload age (via `head()`, null-safe) is at least 1.75 h and no scraper run is queued or in progress. Configure the Cron Trigger. Keep the GitHub schedule as a backup. | High | S | 61% of slots are empty. Everything else depends on runs happening. |
| 2 | **Config-only coverage:** the six Workday URLs; GeoRezo and GSA-UK `[[rss_feeds]]`; a Cadremploi `[[career_sites]]` search page; correct the "not on any supported ATS" comment. **Plus the one-line Workday paging fix:** stop on `len(page) < WORKDAY_PAGE`, and read `total` only from page 0. | High | S | Biggest coverage gain per hour of work; GeoRezo alone is about 40 geomatics jobs a month. |
| 3 | **French survey taxonomy bundle:** French topographic works, survey office software, guarded survey instruments, ROLE_NOUNS additions, the détection de réseaux title term and Network detection domain, and the Marine Surveyor exception. See `taxonomy_spec`. | High | S | Measured: 5 real French géomètre postings change tier. |
| 4 | **Telegram inline buttons on digests** (REVIEW O1), plus the Worker `reply()` 429/unescape fix. See `telegram_spec`. | High (daily UX) | M | Callbacks already reach the Worker, and `mutate()` is reused unchanged. |
| 5 | **Oracle Recruiting Cloud adapter** (WSP, Michael Baker). See `adapter_spec`. | High | M | The largest employer still missing; Canada, UK and Gulf, EN and FR. |
| 6 | **Python prefs conditional write** (If-Match, retry once; boto3 ≥ 1.35.69). | Medium-High (data safety) | S-M | Protects the application log from silent loss. |
| 7 | **Greenhouse `content=true&pay_transparency=true`**, keeping the per-detail path as a TOO_LARGE fallback and formatting `pay_input_ranges` into `salary`. **SmartRecruiters per-board `q=`** for AECOM2. | Medium | S + S | Salary in alerts (O7); AECOM global GIS roles (O8). Revisits O9 for the salary reason only. |
| 8 | **Arabic pattern fixes:** folded `خرايط`, widened GIS and remote-sensing patterns, the `مساحة` (area) fix, Tunisian terms, and Arabic normalisation before term extraction. | Medium (correctness), low reach | S | It is a bug, and the fix is cheap. |
| 9 | **Telegram polish:** silent-notification policy, `<blockquote expandable>` for AI and visa notes (no blank lines inside), `link_preview_options`, `setMyCommands` (en default + fr + ar, scoped to the chat). | Medium | S each | Fewer pings, shorter digests, discoverable commands. |
| 10 | **Taxonomy second wave:** cloud-native formats, OGC services, MapLibre and deck.gl, hydrography domain and software, remote-sensing missions (without "SAR imagery"). | Low-Medium (0 flips; enrichment) | S | Improves "why matched" and the AI context. Not a recall fix. |
| 11 | **Generic-board plumbing**, then add the boards: `pages.py` sitemap `relevant_only`, per-site `max_sitemap_urls`, an iCIMS slug taken from the segment before `/job`, `unquote` on slugs. Then Tanitjobs and Emploitic (Maghreb), Impactpool (with paging), Welcome to the Jungle, Esri Canada (microdata fix in `generic.py`, FR job signals). | Medium | M | Maghreb and international-organisation coverage; low volume but high precision. |
| 12 | **Cheap hardening:** `commands.yml` `queue: max`; write `index.json` compact (`separators=(",",":")`) and pass the parsed index into `answerFast`; log the index and state byte sizes in the run summary with warnings at 1 MB (index) and 8 MB (state); add an `x-amzn-waf-action` check to `http.py`. | Low-Medium | S | Each is a one-liner or close. |
| 13 | **Decide on JobSpy Indeed.** It impersonates the Indeed iOS app with an embedded key, which contradicts the bot's honest-UA policy, in a public repo. Consider `JOBSPY_SITES=linkedin`, or reviewing it (REVIEW:51). | Compliance | S | Your call; flagged, not prescribed. |
| 14 | **Later or optional:** `<tg-time>` deadlines (test rendering on your phone first); paging for `/jobs` and `/range` (O2, after O1); `?start=why_<code>` deep links; UN Careers backend (mostly consultancies today); iCIMS Dewberry and KCI, Jibe (EagleView, Merrick), Pinpoint (Trilon): all US, rarely sponsor; Teamtailor (Nordics, 2-3 items); BambooHR host detection only; `state.json` If-Match; conditional GET. | Low | S-M | Only once the items above are done. |

### Do not do (over-engineering or blocked)

- **D1, KV, Durable Objects, Queues, Actions cache or artifacts** for state or prefs. R2 plus conditional puts is the right design at one user and a few thousand jobs. D1's free cap is 100k rows written per day.
- **Bloom filters, content-hash shards, delta indexes, combined raw snapshots.** There is more than 100x headroom. A Bloom false "seen" would hide a job.
- **Making the keep-alive step conditional.** As proposed it is dead code for `scraper.yml` and would cause an outage around 2026-11-19. Keep it.
- **Per-isolate ETag cache** for the index. It rarely hits for a single user.
- **ReKrute with a spoofed UA; the legacy SuccessFactors XML feed and the CSB RSS** (both disallowed in robots.txt); **Gulf boards** (bot walls); **ESA and EUMETSAT** (nationality rules).

### Checked and dropped

- The researcher's "AWS WAF 405 on iCIMS" (not reproduced on 6 tenants).
- The Greenhouse "5.2 MB per run" cost (the bot fetches the 620 KB list).
- "Queues consumers must be Workers" (HTTP pull consumers exist; the verdict still holds).
- "D1 loses restore" (D1 has Time Travel, 7 days on Free).
- The Telegram "10-15 s callback deadline" (undocumented).
- "Edits are exempt from rate limits" (unsupported).
- Many cited evidence URLs that are now 404, 410 or 403. The affected claims rest only on search snippets and were not used for ranking.

### Doc fixes to bundle

- `README.md:298`: "4-hourly" should be "2-hourly".
- `cloudflare/README.md:47`: `allowed_updates=["message","callback_query"]`, plus `"channel_post"` if a channel is used.
- `config/sources.toml:69-70`: Maxar (now Vantor) and Bentley are on Workday, Carto is on Pinpoint, WSP is on Oracle.

**Main sources:** docs.greenhouse.io/job-board.html; developers.cloudflare.com (/r2/api/s3/api/, /r2/api/error-codes/, /workers/platform/limits/, /d1/platform/pricing/); docs.github.com (events-that-trigger-workflows, control-workflow-concurrency); core.telegram.org/bots/api and /bots/api-changelog; api.github.com/repos/Tlilixmed/geospatial-job-bot/actions/workflows/scraper.yml/runs; the live endpoints quoted above.