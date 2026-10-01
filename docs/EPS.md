# EPS — Engineering Plan & Specification

| | |
|---|---|
| **Project** | EDXSO AI Engineer Intern — Assignment 1 |
| **Document** | Engineering Plan & Specification (EPS) |
| **Version** | 1.0 (baseline) |
| **Date** | 2026-10-01 |
| **Implements** | [PRD.md](PRD.md) v1.0 |
| **Decision log** | [DECISIONS.md](DECISIONS.md) |

This document explains **how** the system is built: architecture, tech stack, data model, module contracts, algorithms, prompts, the sending workflow, error handling, testing and milestones. Requirement IDs (`FR-*`, `NFR-*`) refer to the PRD.

---

## 1. Architecture

```
                         ┌──────────────────────────────────────────────┐
                         │                n8n (self-hosted)              │
                         │  WF1 pipeline_run   WF2 outreach_send   WF3 err│
                         └───────┬───────────────────┬──────────────────┘
                                 │ HTTP + X-API-Key  │
┌──────────┐   ┌─────────────────▼───────────────────▼─────────────────┐   ┌─────────────┐
│ Typer CLI│──▶│                 Python core  (package: outreach)       │◀──│  Streamlit  │
└──────────┘   │                                                        │   │ review + DM │
               │ discovery → metrics → classify → filter → enrich →     │   │ queue +     │
               │ personalize → (review) → sending.claim/result → export │   │ tracker     │
               │                         FastAPI wraps the same funcs   │   └─────────────┘
               └───┬──────────────┬───────────────┬───────────────┬─────┘
                   │              │               │               │
          YouTube Data API   Creator websites   LLM providers   SQLite (source of truth)
          v3 (official)      (robots-aware)     Gemini → Groq    + CSV/XLSX exports
```

**Key principle:** all business logic lives in the Python package. The CLI, the FastAPI service and Streamlit are thin layers over the same functions. n8n handles **orchestration and delivery**: triggers, sequencing, approval-gated sending, SMTP and branching. It holds no business rules, so the system behaves the same with or without n8n (FR-S8).

## 2. Tech Stack (all free)

| Concern | Choice | Notes |
|---|---|---|
| Language / runtime | Python 3.12 | |
| Env & packaging | `uv` + `pyproject.toml` | One-command setup |
| Config | `pydantic-settings` (`.env`) + YAML (`config/*.yaml`) | Secrets in `.env`, rules in YAML |
| Data models | Pydantic v2 | Typed contracts between modules |
| DB / ORM | SQLite + SQLModel | Moving to Postgres is just a DSN change |
| HTTP | `httpx` + `tenacity` | Timeouts, retries, backoff |
| YouTube | Official Data API v3 through `httpx` (REST) | Explicit quota accounting |
| Scraping | `beautifulsoup4`, `urllib.robotparser` | Only the creator's own websites |
| Email validation | `email-validator` + `dnspython` (MX lookup) | |
| LLM | `openai` Python SDK against **OpenAI-compatible endpoints** | Gemini and Groq both expose one |
| API | FastAPI + Uvicorn | Called by n8n |
| CLI | Typer | `outreach run`, `outreach send`, … |
| UI | Streamlit | Review, DM queue, tracker |
| Orchestration | n8n Community Edition (`npx n8n`, Node 22) | Free self-hosted; no Docker needed (D-13) |
| Email | Gmail SMTP (`smtp.gmail.com:587`, app password) | Used by both the n8n SMTP node and Python `smtplib` |
| Exports | pandas + openpyxl | CSV + XLSX |
| Quality | pytest, ruff, mypy (light) | |

## 3. Repository Layout

```
exdso-assignment/
├── README.md
├── pyproject.toml
├── .gitignore
├── config/
│   ├── .env.example                # copy to config/.env (git-ignored) and fill in
│   ├── settings.yaml               # niche, keywords, thresholds, weights, limits
│   └── brand.yaml                  # brand persona, product, value props, angles
├── prompts/
│   ├── classify_v1.md
│   └── outreach_v1.md
├── src/outreach/
│   ├── config.py
│   ├── models.py                   # SQLModel tables + Pydantic DTOs
│   ├── db.py                       # engine, session, migrations-lite
│   ├── sources/youtube.py          # API client + QuotaTracker
│   ├── discovery.py
│   ├── metrics.py
│   ├── llm/{client.py,cache.py,schemas.py}
│   ├── classify.py
│   ├── filtering/{rules.py,scoring.py}
│   ├── enrichment/{emails.py,website.py,socials.py}
│   ├── personalize/{angle.py,generator.py,validators.py,similarity.py}
│   ├── sending/{claim.py,smtp.py,dm_queue.py}
│   ├── exports.py
│   ├── pipeline.py                 # stage runner, job tracking
│   ├── api/main.py                 # FastAPI
│   └── cli.py                      # Typer
├── app/streamlit_app.py
├── n8n/workflows/{pipeline_run.json,outreach_send.json,error_handler.json}
├── outputs/                        # committed deliverable exports
├── data/                           # git-ignored (SQLite db, caches)
├── tests/
└── docs/{PRD.md,EPS.md,DECISIONS.md,PHASES.md,SETUP.md,screenshots/}
```

## 4. Configuration

`config/.env` (secrets and environment; the template is `config/.env.example`):
```
YOUTUBE_DATA_API_KEY=
LLM_PROVIDERS=gemini,groq                  # fallback order
GEMINI_API_KEY=      GEMINI_MODEL=<flash model id>
GROQ_API_KEY=        GROQ_MODEL=<model id>
SMTP_HOST=smtp.gmail.com  SMTP_PORT=587  SMTP_USER=  SMTP_APP_PASSWORD=
SEND_MODE=DRY_RUN                          # DRY_RUN | REDIRECT | LIVE
TEST_INBOX=                                # REDIRECT target
LIVE_ALLOWLIST=                            # LIVE only sends to these addresses
AUTO_APPROVE=false
API_KEY=                                   # shared secret for n8n → FastAPI
DATABASE_URL=sqlite:///data/outreach.db
```

`config/settings.yaml` (rules, all versioned through `rules_version`):
```yaml
campaign_id: edtech-2026-q4
niche: { name: "Technology & EdTech", accepted: [Technology, EdTech] }
discovery:
  queries: ["python tutorial for beginners", "learn coding", "AI tools explained", ...]  # ~30
  published_after_days: 90
  max_pages_per_query: 1
  target_candidates: 200
  recent_videos: 10
filters:
  subscribers: { min: 5000, max: 100000 }
  min_engagement_rate: 0.02
  active_within_days: 90
  min_sample_videos: 3
  min_relevance: 0.6
  geography_allowlist: []      # empty = no geo filter
  languages: [en, hi-en]
scoring_weights: { relevance: 35, engagement: 25, size_fit: 15, activity: 15, contactability: 10 }
llm: { temperature_classify: 0.1, temperature_generate: 0.7, max_retries: 2, rpm: { gemini: 8, groq: 25 } }
```

## 5. Data Model (SQLite)

| Table | Purpose | Key columns / constraints |
|---|---|---|
| `runs` | One row per pipeline run | `id`, `started_at`, `finished_at`, `status`, `stages`, `stage_reports` (counts per stage), `youtube_quota_used`, `failure` |
| `search_query_log` | Queries already searched, so re-runs never re-spend 100 units per query | `query` PK, `searched_at`, `channel_ids_found` |
| `creators` | Canonical creator record | `id`, `platform`, `platform_id`, **UNIQUE(platform, platform_id)**, `handle`, `name`, `profile_url`, `country`, `subscriber_count`, `subscribers_hidden`, `video_count`, `description`, `uploads_playlist_id`, `topic_categories`, `discovered_via_query`, `first_seen_run_id`, `videos_fetched_at` |
| `videos` | Recent uploads | `video_id` PK, `creator_id` FK, `title`, `description`, `published_at`, `duration_s`, `views`, `likes` (nullable = hidden), `comments` |
| `metrics` | Derived metrics | `creator_id` PK, `er`, `er_method`, `sample_size`, `median_views`, `views_per_sub`, `last_upload_at`, `uploads_90d` |
| `classifications` | LLM output | `creator_id` PK, `primary_niche`, `sub_niches`, `content_themes`, `tone`, `audience_level`, `language`, `relevance`, `brand_safety_flags`, `evidence`, `model`, `prompt_version` |
| `filter_results` | Pass/fail per run | `creator_id`, `run_id`, `status` (QUALIFIED/REJECTED), `score`, `reasons_json`, `rules_version` |
| `contacts` | Enrichment | `creator_id` PK, `email` (or `Not Found`), `email_status` (FOUND/NOT_FOUND/INVALID), `email_source_type`, `email_source_url`, `mx_valid`, `instagram`, `tiktok`, `x`, `linkedin`, `website` |
| `messages` | Generated outreach | `id`, `creator_id`, `campaign_id`, **UNIQUE(creator_id, campaign_id)**, `angle`, `email_subject`, `email_body`, `email_words`, `dm`, `dm_words`, `signals_json`, `validation_json`, `attempts`, `provider`, `model`, `prompt_version`, `review_status` (GENERATED/NEEDS_REVIEW/APPROVED/REJECTED), `reviewed_at` |
| `outreach` | One row per deliverable | `id`, `creator_id`, `campaign_id`, `channel` (EMAIL/IG_DM), `recipient`, `recipient_norm`, **UNIQUE(campaign_id, channel, creator_id)**, **UNIQUE(campaign_id, channel, recipient_norm)**, `status` (QUEUED/SENDING/SENT/SIMULATED/FAILED/SKIPPED/MANUAL_SENT), `mode`, `attempts`, `claimed_at`, `sent_at`, `provider_msg_id`, `error` |
| `outreach_events` | Append-only log | `id`, `outreach_id`, `ts`, `event`, `detail_json` |
| `llm_cache` | Saves quota on re-runs | `key` = sha256(provider+model+prompt_version+input), `response_json`, `created_at` |
| `jobs` | Async stage jobs for n8n | `id`, `stage`, `run_id`, `status`, `progress`, `error`, timestamps |
| `pipeline_errors` | Per-creator failures that did not stop the run | `id`, `run_id`, `creator_id`, `stage`, `message`, `occurred_at` |

Sentinel strings: `Not Found` is used for emails only (we searched and found nothing). `Not Available` is used for data the platform doesn't expose (demographics, hidden counts).

## 6. Pipeline Stages — Specifications

Every stage is **idempotent** and **resumable**. It selects only creators that are missing *its own output* (e.g. metrics processes creators with `videos_fetched_at` set and no `creator_metrics` row), commits after each unit of work, records per-creator failures in `pipeline_errors`, and never stops the whole run because one creator failed (NFR-2).

### 6.1 Discovery (`discovery.py`, FR-D1–D6)
1. For each query, call `search.list` with `type=video`, `order=relevance`, `publishedAfter=now-90d`, `maxResults=50`, and collect `channelId`s. Searching *videos* instead of channels biases results toward creators who are active now.
2. De-duplicate against `creators` (UNIQUE key) and stop once `target_candidates` is reached.
3. Call `channels.list` (`snippet,statistics,contentDetails,brandingSettings,topicDetails`) in batches of 50.
4. **Cheap pre-filter before spending more quota:** if subscribers are outside 5k–100k or hidden, reject straight away with a reason code and skip fetching videos.
5. For the rest: `playlistItems.list` (uploads playlist) gives the last N video IDs, then `videos.list` (`snippet,statistics,contentDetails`) in batches of 50.

**Quota budget** (default 10,000 units/day):

| Call | Unit cost | Calls (≈200 candidates) | Units |
|---|---|---|---|
| `search.list` | 100 | ~30 | ~3,000 |
| `channels.list` | 1 | ~5 | 5 |
| `playlistItems.list` | 1 | ~120 (after pre-filter) | 120 |
| `videos.list` | 1 | ~120 | 120 |
| **Total** | | | **≈ 3,250** (headroom for re-runs) |

`QuotaTracker` adds up the units used, persists them to `runs.quota_used`, and raises `QuotaBudgetExceeded` at a configurable ceiling (default 8,000). The run then ends cleanly as `PARTIAL`.

### 6.2 Metrics (`metrics.py`)
- **Sample:** the last 10 public uploads, excluding live/upcoming streams and videos under 48 hours old (their stats haven't settled). Long-form videos (> 180 s) are preferred; if fewer than 3 exist, all uploads are used and `er_method` is flagged `mixed_shorts`.
- **Engagement rate (primary):** `ER = median over sample of (likes + comments) / views`. The median resists one viral outlier.
- **Hidden likes:** ER is computed only over videos with visible likes. If fewer than `min_sample_videos` (3) have visible likes, ER = `Not Available` with the reason ("Likes hidden on 8/8 sampled videos") and the creator fails rule `R_ER_UNAVAILABLE`. We never estimate it.
- **Secondary:** `views_per_sub = median_views / subscribers` (reach health), `uploads_90d`, `last_upload_at`.

### 6.3 Classification (`classify.py`, FR-F4)
- Input: channel name, description, `topicDetails` categories, and the last 10 titles plus truncated descriptions.
- One LLM call per creator at temperature 0.1, returning JSON that matches `ClassificationSchema`:
  ```json
  { "primary_niche": "Technology|EdTech|Other:<x>", "sub_niches": [], "content_themes": ["3-5 short themes"],
    "tone": "e.g. casual, explanatory, humorous", "audience_level": "student|beginner|professional|mixed",
    "language": "en|hi-en|hi|other", "relevance": 0.0, "brand_safety_flags": [],
    "evidence": ["quoted titles that justify the label"] }
  ```
- The output is checked against the schema. If it is invalid, the call is retried. If it still fails after retries, the creator is marked `R_CLASSIFY_FAILED` (rejected with that reason, not dropped).
- Brand-safety flags also get a deterministic keyword check (e.g. "crack", "mod apk", "betting", "hack account"), so safety doesn't rely on the LLM alone.

### 6.4 Filtering & Scoring (`filtering/`, FR-F1–F9)
All rules are evaluated, rather than stopping at the first failure, so every applicable reason is reported.

| Code | Rule | Reason text example |
|---|---|---|
| `R_SUBS_HIDDEN` | subscriber count visible | "Subscriber count hidden by creator" |
| `R_SUBS_RANGE` | 5,000 ≤ subs ≤ 100,000 | "142,300 subscribers > 100,000 max" |
| `R_INACTIVE` | upload within 90 days | "Last upload 2026-03-02 (213 days ago)" |
| `R_SAMPLE` | ≥ 3 sample videos | "Only 2 eligible videos" |
| `R_ER_UNAVAILABLE` | ER computable | "Likes hidden on 7/10 videos" |
| `R_ER_LOW` | ER ≥ 2.0% | "Engagement 1.1% < 2.0% min" |
| `R_NICHE` | primary niche ∈ accepted | "Classified as Gaming" |
| `R_RELEVANCE` | relevance ≥ 0.6 | "Relevance 0.42 < 0.60" |
| `R_BRAND_SAFETY` | no flags | "Flag: promotes cracked software" |
| `R_GEO` | country in allowlist (if configured) | "Country DE not in allowlist" |
| `R_LANGUAGE` | language in allowed list | "Primary language: es" |

**Brand-fit score (0–100), qualified creators only:**
- relevance × 35
- engagement: `min(ER / 0.08, 1)` × 25
- size fit: a triangular curve peaking at 10k–50k, × 15
- activity: `min(uploads_90d / 6, 1)` × 15
- contactability: 10 if a valid email was found

The score ranks the shortlist. The *contactability* term is filled in after enrichment, so the score is recomputed then.

### 6.5 Enrichment (`enrichment/`, FR-E1–E6)
**Email search (stops at the first valid hit, recording its source):**
1. Channel description, then recent video descriptions: regex, plus de-obfuscation (`name [at] domain [dot] com`, `name(at)domain.com`).
2. Collect links from the descriptions and sort them:
   - social links → stored as socials
   - link aggregators (linktr.ee, bio.link, beacons) → fetched only if `robots.txt` allows
   - anything else → treated as the creator's own website
3. On the website (if `robots.txt` allows): fetch `/`, then any `contact`/`about`/`collab`/`work-with-me` links found on that page, at most 4 pages. Extract `mailto:` links and regex matches.

**Validation:**
- `email-validator` syntax check
- reject junk: `example.com`, image-style false positives (`logo@2x.png`), `noreply@`, `sentry`/`wixpress` system addresses
- the domain must have an MX record (`dnspython`)

Results become `FOUND`, `INVALID` (store the reason; the value stays `Not Found`) or `NOT_FOUND`.

**Hard rules:**
- Never construct or guess an address.
- Never click or solve YouTube's "View email address" captcha.
- HTTP politeness: timeout 10 s, User-Agent identifying the project, at most 1 request/sec per domain.

**Other fields:**
- socials: Instagram, TikTok, X, LinkedIn and website, taken from links
- geography: `snippet.country` or `Not Available`
- audience age/gender: `Not Available`
- audience level: from the classification, labelled *(inferred)*

### 6.6 Personalization (`personalize/`, FR-P1–P7)

**Angle selection (deterministic, `angle.py`):**

| Condition (first match wins) | Angle |
|---|---|
| ER ≥ 6% and uploads_90d ≥ 6 | Brand ambassador program |
| Themes include tutorials/how-to and subs ≥ 20k | Sponsored integration |
| Themes include reviews/tools/comparisons | Paid product placement |
| Audience level = student/beginner | Affiliate campaign (student discount code) |
| subs < 15k | UGC content creation (paid) |
| fallback | Barter collaboration (free premium access) |

**Generation:** one LLM call per creator at temperature 0.7 (`prompts/outreach_v1.md`), returning:
```json
{ "email_subject": "...", "email_body": "...", "dm": "...",
  "signals_used": ["recent_video", "niche", "tone", "audience", "angle", "value_prop"],
  "referenced_video_title": "exact title or null" }
```

**Prompt design (summary):**
- System role: a brand partnerships manager writing like a person.
- Inputs: a structured creator brief (name, niche, themes, tone, audience level, **two real recent titles with dates**, country), the brand brief from `brand.yaml`, the chosen angle, and hard constraints (word ranges, no placeholders, no invented stats or facts, mention exactly one specific video, end the email with one clear call to action).
- Two short contrasting examples teach style, not template. They use a fictional creator so they can't be copied.

**Validators (`validators.py`); each failure produces a feedback string:**
- `email_body` 60–90 words; `dm` 15–30 words (`\b\w+\b` tokenizer, documented)
- creator name or channel name appears in both
- `referenced_video_title` matches a real title in `videos` (fuzzy ratio ≥ 0.85), or is null and at least 2 other signals were used
- no `[`/`{`/`<` placeholders, no "Dear Sir/Madam", no banned clichés ("hope this email finds you well", "I came across your profile")
- no numbers in the text other than those in the input (prevents invented stats)

**Retry policy:** on failure, re-prompt with the validator feedback, up to 2 times. After that, store the best attempt with `review_status=NEEDS_REVIEW`.

**Similarity check (`similarity.py`):** word-trigram Jaccard similarity across all email bodies. Pairs above 0.5 are flagged in the review UI and the run summary.

**Footer:** a fixed signature plus one opt-out line is appended after validation and is not counted toward the 60–90 words (PRD A-3).

### 6.7 LLM Client (`llm/client.py`)
- One `LLMClient.complete_json(schema, messages, temperature)` interface.
- Providers are configured as `{base_url, api_key, model, rpm}` and called through the `openai` SDK at their OpenAI-compatible endpoints.
- **Fallback chain:** Gemini → Groq. On 429/5xx/timeout: exponential backoff (tenacity, 3 tries), then move to the next provider.
- **Client-side rate limiter:** a token bucket per provider, set below the published RPM.
- **Cache:** responses are stored in `llm_cache`, so re-runs and resumes cost nothing.
- JSON mode where the provider supports it; otherwise extract the first JSON object and validate it against the schema. Invalid JSON counts as a retryable failure.
- Each call records provider, model, latency, tokens (if returned) and outcome, and these feed the run summary.

## 7. Review & Manual DM Queue (Streamlit, FR-R1–R2, FR-S9)

Pages:
1. **Dashboard:** run stats, funnel (discovered → metrics → qualified → email found → generated → approved → sent), rejection-reason breakdown, email hit rate.
2. **Creators:** a filterable table of all records with status and reasons.
3. **Review queue:** each message next to the creator brief and the signals used, with similarity warnings. Actions: Approve, Edit (re-validates), Reject.
4. **DM queue:** the DM text with a copy button, the Instagram link if found (otherwise "No Instagram handle found"), and a **Mark sent manually** button that writes `outreach(channel=IG_DM, status=MANUAL_SENT)` plus an event.
5. **Tracker:** the outreach table and event log, with a download button for exports.

## 8. Backend API (FastAPI) — n8n contract

All endpoints need the `X-API-Key` header.

| Method & path | Purpose | Response |
|---|---|---|
| `POST /runs` | Start a run (snapshots config) | `{run_id}` |
| `POST /runs/{run_id}/stages/{stage}` | Start a stage asynchronously (`discover`, `metrics`, `classify`, `filter`, `enrich`, `generate`, `export`) | `202 {job_id}` |
| `GET /jobs/{job_id}` | Poll job status | `{status, progress, error}` |
| `GET /runs/{run_id}/summary` | Funnel and stats | JSON |
| `POST /outreach/claim?channel=EMAIL&limit=10` | **Atomically** claim sendable items (see §9) | `[{outreach_id, to, original_to, subject, body, mode, idempotency_key}]` |
| `POST /outreach/{id}/result` | Report the outcome | `{status}` |
| `GET /health` | Liveness, DB check, mode | JSON |

## 9. Sending Layer (FR-S1–S8)

### 9.1 Claim logic (`sending/claim.py`), in one DB transaction
1. Create any missing `outreach` rows (`QUEUED`) for creators that have `messages.review_status=APPROVED` and `contacts.email_status=FOUND`. The UNIQUE constraints make duplicate rows impossible: `INSERT … ON CONFLICT DO NOTHING`.
2. `UPDATE outreach SET status='SENDING', claimed_at=now, attempts=attempts+1 WHERE status IN ('QUEUED') OR (status='FAILED' AND attempts<3) … LIMIT n RETURNING …`.
3. Stale claims (`SENDING` for more than 10 minutes) are released back to `QUEUED`. This handles crashes.
4. Apply `SEND_MODE`:
   - `DRY_RUN`: `to` = original. The caller must not send, and reports `SIMULATED`.
   - `REDIRECT`: `to` = `TEST_INBOX`. The subject is prefixed `[TEST → original@x.com]`.
   - `LIVE`: only if the recipient is in `LIVE_ALLOWLIST`; otherwise `SKIPPED`.
5. Already `SENT`/`SIMULATED`/`MANUAL_SENT` rows are never claimed again, which is what prevents duplicates (FR-S5).

`POST /outreach/{id}/result` writes the final status, `sent_at`, `provider_msg_id` or `error`, plus an `outreach_events` row (FR-S4, FR-S6).

### 9.2 n8n workflows (exported to `n8n/workflows/`)

**WF1 `pipeline_run`:**
1. Manual Trigger (or Schedule) → `POST /runs`
2. For each stage `[discover, metrics, classify, filter, enrich, generate, export]`: `POST stage` → loop {Wait 10 s → `GET /jobs/{id}`} until `done`/`failed`
3. On `failed`: stop and route to WF3
4. `GET /summary` → a Set node formats a run report (optionally emailed to the developer)

**WF2 `outreach_send`:**
1. Schedule Trigger (every 5 min) or Manual → `POST /outreach/claim?limit=10`
2. Split In Batches (1) → IF `mode == DRY_RUN`
   - true → Set `{status: SIMULATED}`
   - false → **Send Email (SMTP)** node (Gmail credentials) → Set `{status: SENT, provider_msg_id}`
3. The SMTP node's error output → Set `{status: FAILED, error}`
4. `POST /outreach/{id}/result` → Wait 2 s (throttle) → next item
5. Gmail send-volume limits are respected by `limit` and the throttle.

**WF3 `error_handler`:** an Error Trigger logs the failing workflow and node to the backend (`POST /events`) and optionally emails the developer.

The n8n credentials (SMTP, API key header) are created in the n8n UI. Exported JSON files have their credentials stripped. The README documents the import and credential steps, with screenshots.

**Networking:** n8n runs locally with `npx n8n` (UI at `localhost:5678`). The backend runs with `uv run uvicorn …` at `http://localhost:8000`. Both are on the same host, so no Docker networking is needed (D-13).

### 9.3 CLI fallback (FR-S8)
`outreach send --channel email` uses the same `claim()`, `smtplib` and `report_result()` functions, so the logic and guarantees are identical without n8n.

## 10. CLI

```
outreach init-db
outreach run [--stages discover,metrics,...] [--limit N]   # full or partial pipeline
outreach send [--mode DRY_RUN|REDIRECT] [--limit N]
outreach approve --all-valid                               # demo helper (validated only)
outreach export                                            # → outputs/*.csv|xlsx
outreach summary [--run-id]
```

## 11. Outputs (FR-X1–X4)

| File | Columns |
|---|---|
| `outputs/influencers.csv/.xlsx` | Name, Platform, Followers, Engagement Rate, Niche, Sub-niches, Content Themes, Email, Email Source, Profile URL, Instagram, Website, Country, Audience Age, Audience Gender, Status, Score, Reasons |
| `outputs/messages.csv/.xlsx` | Name, Angle, Email Subject, Email Body, Email Words, DM, DM Words, Signals Used, Model, Prompt Version, Review Status |
| `outputs/outreach_tracker.csv/.xlsx` | Influencer, Email, Message Generated, Sent, Date, Status, Mode, Channel, Attempts, Error |
| `outputs/run_summary.json` | Funnel counts, email hit rate, rejection breakdown, LLM stats, quota used |

## 12. Error Handling Matrix (NFR-3)

| Failure | Detection | Handling | Record |
|---|---|---|---|
| YouTube 403 quotaExceeded | HTTP status/reason | Stop discovery cleanly; run marked `PARTIAL` | `runs.status`, summary |
| YouTube 5xx / timeout | httpx exception | 4 attempts with exponential backoff; then that creator is left unmarked and retried next run | `pipeline_errors` |
| Malformed API item | `MalformedYouTubeData` | That creator is marked failed; the stage continues | `pipeline_errors` |
| Hidden subscribers / likes | Missing fields | Reason codes `R_SUBS_HIDDEN` / `R_ER_UNAVAILABLE` | filter_results |
| Website blocked / robots disallow / 4xx | robotparser / status | Skip the site; email stays `Not Found` | contacts.email_source_type=`blocked` |
| LLM 429 / 5xx | status | Backoff, then the next provider in the chain | llm stats |
| LLM invalid JSON / schema | Pydantic | Retry (counts toward max_retries) | messages.validation_json |
| Message fails validators | validators | Re-prompt with feedback ×2, then `NEEDS_REVIEW` | messages |
| SMTP auth / send error | node/smtplib error | `FAILED` + error; retried up to 3 attempts | outreach + events |
| Process crash mid-send | stale `SENDING` | Released after 10 min | events |
| Bad config | pydantic-settings at startup | Fail fast with a clear message | stderr |

## 13. Testing Strategy

| Layer | Tests |
|---|---|
| Unit | ER computation (hidden likes, shorts fallback), each filter rule and its reason text, scoring, email regex/de-obfuscation/junk filter, angle selection, word counter, every validator, similarity |
| Contract | YouTube client against recorded JSON fixtures (no live quota use in tests) |
| LLM | Generator with a fake provider: retry-on-validation-failure and fallback-on-429 paths |
| Sending | Claim is atomic and idempotent: claiming twice returns no duplicates; `SENT` is never re-claimed; REDIRECT rewrites the recipient; LIVE without allowlist → `SKIPPED` |
| End-to-end | `outreach run --limit 10` against live APIs (manual, before the demo) |

Tests run offline with `pytest`. Coverage focuses on rules, validators and sending, the parts that matter most for correctness.

## 14. Milestones (≈ 7 working days; adjust to OQ-2)

These milestones are grouped into 4 delivery phases with checklists and exit criteria in [PHASES.md](PHASES.md). External setup is in [SETUP.md](SETUP.md).

| # | Milestone | Output | Exit criteria |
|---|---|---|---|
| M0 | Setup & docs | Repo, `uv`, config, DB models, PRD/EPS/DECISIONS | `outreach init-db` works |
| M1 | Discovery + metrics | `sources/youtube.py`, `discovery.py`, `metrics.py` | ≥ 200 candidates, quota logged |
| M2 | LLM client + classification + filtering | `llm/`, `classify.py`, `filtering/` | Every creator QUALIFIED/REJECTED with reasons |
| M3 | Enrichment | `enrichment/` | Email hit rate reported; zero guessed emails |
| M4 | Personalization | `personalize/`, prompts | ≥ 95% pass validators; similarity report |
| M5 | API + sending + n8n | FastAPI, `sending/`, WF1–WF3 | Double send-run → 0 duplicates; REDIRECT mail arrives in the test inbox |
| M6 | Streamlit | Review, DM queue, tracker | Approve → send → tracker updates |
| M7 | Hardening & submission | Tests, exports, README, screenshots, demo video | Fresh-clone setup ≤ 15 min |

Documentation is kept up to date in every milestone. Each non-obvious choice gets a DECISIONS.md entry when it is made.

## 15. Scalability Path (50 → 500+, NFR-4)

- **Quota:** spread discovery across days (incremental runs plus a UNIQUE creator key); refresh known channels with `channels.list` (1 unit) instead of searching again; request a quota increase from Google (free) if needed.
- **Throughput:** stages already work per creator in batches; add a worker pool (asyncio) under the per-provider rate limiter.
- **Storage:** SQLite → Postgres by changing `DATABASE_URL` (SQLModel); the claim query uses `FOR UPDATE SKIP LOCKED` on Postgres.
- **Orchestration:** n8n queue mode with Redis workers (still free when self-hosted).
- **New platforms or niches:** add a `sources/<platform>.py` that implements the same `SourceClient` protocol, and a new niche block in `settings.yaml`. No changes are needed in filtering, personalization or sending.
