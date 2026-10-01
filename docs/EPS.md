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
├── pyproject.toml / uv.lock        # Python 3.12, pinned dependencies
├── .github/workflows/ci.yml        # lint + offline tests on every push
├── config/
│   ├── .env.example                # copy to config/.env (git-ignored) and fill in
│   ├── settings.yaml               # niche, queries, thresholds, weights, limits
│   └── brand.yaml                  # brand persona, value props, offer per angle
├── prompts/{classify_v1.md,outreach_v1.md}   # versioned prompts
├── src/outreach/
│   ├── config.py                   # Secrets (.env) + Settings/Brand (YAML), validated
│   ├── models.py  db.py            # SQLModel tables, engine (WAL, UTC handling)
│   ├── stage.py   pipeline.py      # stage contract; start_run/execute_stage/finish_run
│   ├── quota.py                    # YouTube quota-day accounting across runs
│   ├── sources/youtube.py          # Data API v3 client + QuotaTracker
│   ├── discovery.py  metrics.py
│   ├── llm/{client.py,cache.py,types.py}     # fallback chain, circuit breaker, cache
│   ├── prompts.py  classify.py
│   ├── filtering/{rules.py,scoring.py,stage.py}
│   ├── enrichment/{emails.py,links.py,website.py,stage.py}
│   ├── personalize/{angle.py,brief.py,draft.py,validators.py,similarity.py,generator.py,review.py}
│   ├── sending/{queue.py,compose.py,smtp.py,dispatch.py,dm_queue.py}
│   ├── reporting.py  exports.py  jobs.py
│   ├── api/main.py                 # FastAPI for n8n
│   └── cli.py                      # Typer
├── app/{streamlit_app.py,console_pages.py}
├── n8n/workflows/{pipeline_run.json,outreach_send.json,error_handler.json}
├── outputs/                        # committed deliverables
├── data/                           # git-ignored SQLite database
├── tests/                          # offline: fixtures, fakes, in-memory DBs
└── docs/{PRD,EPS,DECISIONS,PHASES,SETUP}.md
```

## 4. Configuration

`config/.env` (secrets and environment; the template is `config/.env.example`):
```
YOUTUBE_DATA_API_KEY=
LLM_PROVIDERS=gemini,groq                  # fallback order
GEMINI_API_KEY=      GEMINI_MODELS=gemini-2.5-flash,gemini-3.1-flash-lite   # ordered fallback list
GROQ_API_KEY=        GROQ_MODELS=openai/gpt-oss-120b
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
- One `LlmClient.complete_json(request, schema)` interface; the reply is validated against a Pydantic schema.
- `GEMINI_MODELS` / `GROQ_MODELS` are ordered lists, and **each model is its own fallback step** (D-18). Default chain: `gemini-2.5-flash` → `gemini-3.1-flash-lite` → Groq `openai/gpt-oss-120b`.
- Each step has **client-side pacing** below its free-tier requests/minute, **retries** (3 attempts, exponential backoff) for 429/5xx/timeouts/malformed JSON, and a **circuit breaker**: after 2 consecutive failures the step is skipped for 5 minutes.
- **Cache:** successful replies are stored in `llm_cache`, keyed by a SHA-256 of prompt version, temperature and messages, so re-runs and resumes cost nothing.
- Provider, model and prompt version are stored with every classification and message.

## 7. Review & Manual DM Queue (Streamlit, FR-R1–R2, FR-S9)

`uv run streamlit run app/streamlit_app.py`. Pages (each a function in `app/console_pages.py`):
1. **Dashboard:** stat tiles, funnel bars, rejection-reason bars.
2. **Creators:** filterable table with status, score and reasons.
3. **Review queue:** each message next to the angle reason, referenced video, signals, validation issues and similarity warning. Actions: Save edit (re-validates), Approve (blocked while issues exist), Reject, and bulk-approve validated drafts.
4. **Instagram DMs:** DM text with a copy button, profile link, and **Mark as sent** (recorded once as `MANUAL_SENT`). Creators without a handle show the reason.
5. **Outreach tracker:** tiles plus the tracker table (same rows as the CSV export).

## 8. Backend API (FastAPI) — n8n contract

`uv run outreach api` (port 8000). Every route except `/health` requires `X-API-Key`. The API returns 503 if `API_KEY` is unset (D-22).

| Method & path | Purpose | Response |
|---|---|---|
| `GET /health` | Liveness and current send mode | `{status, send_mode}` |
| `POST /runs` | Start a run (`{"stages": [...]}`) | `201 {run_id}` |
| `POST /runs/{run_id}/stages/{stage}` | Start one stage as a background job | `202 {job_id}`; 409 if a job is active; 404 unknown run |
| `GET /jobs/{job_id}` | Poll a job | `{status: QUEUED/RUNNING/SUCCEEDED/FAILED, report, error}` |
| `POST /runs/{run_id}/finish` | Close the run (COMPLETED / PARTIAL / FAILED from its jobs) | `{status}` |
| `GET /summary` | Funnel and rejection reasons | JSON |
| `POST /outreach/claim?limit=10` | **Atomically** claim sendable emails (§9) | `[{outreach_id, deliver_to, intended_recipient, subject, body, mode, method, idempotency_key}]` |
| `POST /outreach/{id}/result` | Report `SENT` / `SIMULATED` / `FAILED` | `{status}`; 409 if not currently `SENDING` |
| `POST /events/workflow-error` | Record an n8n failure | `201` |

## 9. Sending Layer (FR-S1–S8)

### 9.1 Queue logic (`sending/queue.py`)
1. **Queue:** approved messages with a `FOUND` email are inserted as `QUEUED` with `INSERT … ON CONFLICT DO NOTHING`.
   - `UNIQUE(campaign, channel, creator)` stops a creator being queued twice.
   - `UNIQUE(campaign, channel, recipient)` stops one address getting two emails, even if two creators share it.
2. **Release stale claims:** rows stuck in `SENDING` longer than 10 minutes (a crashed sender) go back to `QUEUED`.
3. **Atomic claim:** one statement, `UPDATE outreach SET status='SENDING', attempts=attempts+1 WHERE id IN (SELECT id … status QUEUED or FAILED with attempts < 3 … LIMIT n) RETURNING id`. A row can only enter `SENDING` once, so the CLI and n8n can never claim the same email.
4. **Route by `SEND_MODE`** (`route()`):
   - `DRY_RUN` → `method=simulate`
   - `REDIRECT` → deliver to `TEST_INBOX` with subject `[TEST → creator@x.com] …`; a missing `TEST_INBOX` fails safely
   - `LIVE` → only allowlisted recipients; everyone else is `SKIPPED`
5. **Compose:** the signature and opt-out line are appended after validation (`compose.py`).
6. **Record result:** only accepted from `SENDING`, so a second report for the same email is rejected. The status, timestamp, message ID or error, and an `outreach_events` row are written.

### 9.2 n8n workflows (`n8n/workflows/`)
Each workflow starts with a `Config` node holding the API base URL. API calls use an n8n **Header Auth** credential and SMTP uses an n8n **SMTP** credential (D-24).

**WF1 `pipeline_run`:** Manual trigger → Config → `POST /runs` → Code node (one item per stage) → **Loop Over Items**. For each stage:
1. `POST /runs/{id}/stages/{stage}`
2. Wait 10 s → `GET /jobs/{id}`
3. If still running, go back to the Wait.
4. If succeeded, move to the next stage. If failed, a **Stop and Error** node fires, which triggers WF3.

After the loop: `POST /runs/{id}/finish` → `GET /summary`.

**WF2 `outreach_send`:** Schedule (every 5 min) or Manual → Config → `POST /outreach/claim?limit=10` → IF `method == simulate`:
- **true** → report `SIMULATED`.
- **false** → **Send Email** (Gmail SMTP) node with error output enabled:
  - success → report `SENT` with the SMTP Message-ID
  - error output → report `FAILED` with the error message

Throughput is capped at 10 per run every 5 minutes, which is well inside Gmail's limits.

**WF3 `error_handler`:** Error Trigger → `POST /events/workflow-error`. Set it as the Error Workflow of WF1 and WF2.

**Networking:** n8n runs with `npx n8n` (`localhost:5678`); the API runs with `uv run outreach api` (`localhost:8000`) (D-13).

### 9.3 Python sender (FR-S8)
`uv run outreach send` drains the same queue through the same `claim_emails()` / `record_result()` functions, so the guarantees are identical without n8n.

## 10. CLI

```
outreach check-env                     # which secrets are set (never their values)
outreach init-db
outreach run [--stages a,b,...] [--limit N]   # stages: discover, metrics, classify, filter,
                                              #         enrich, generate, export (default: all)
outreach summary                       # funnel + recent runs
outreach approve                       # approve drafts that passed every validator
outreach send                          # send/simulate approved emails per SEND_MODE
outreach export                        # write outputs/
outreach api                           # start the API for n8n
```

## 11. Outputs (FR-X1–X4)

Written by `outreach export` (or the `export` stage) to `outputs/`:

| File | Contents |
|---|---|
| `influencers.csv` | Every creator in the 5k–100k range: Name, Platform, Profile URL, Followers, Engagement Rate (+ method), Niche, Sub-niches, Content Themes, Tone, Audience Level (inferred), Language, Relevance, Email (+ source and source URL), Instagram/TikTok/X/LinkedIn/Website, Audience Geography/Age/Gender, Status, Score, Reasons |
| `discovered_channels.csv` | Every discovered channel (including out-of-range ones) with its status and reason |
| `messages.csv` | Angle and why, subject, email body, DM, word counts, referenced video, signals, validation issues, attempts, similarity warning, model, prompt version, review status |
| `outreach_tracker.csv` | Influencer, Email, Message Generated, Review Status, Sent, Date, Status, Mode, Attempts, Error, Instagram DM Sent, DM Date |
| `outreach_results.xlsx` | All of the above as sheets |
| `run_summary.json` | Funnel, rejection reasons, email hit rate and sources, models used, quota used today |

## 12. Error Handling Matrix (NFR-3)

| Failure | Detection | Handling | Record |
|---|---|---|---|
| YouTube quota (ours or Google's) | `QuotaTracker` / `quotaExceeded` | Stage stops cleanly; run marked `PARTIAL`; re-run resumes | `runs` |
| YouTube 429 / 5xx / network | status / httpx error | 4 attempts with exponential backoff; creator retried next run | `pipeline_errors` |
| Malformed API item (e.g. premiere without duration) | `MalformedYouTubeData` | That creator fails; stage continues | `pipeline_errors` |
| Hidden subscribers / likes | missing fields | Reason codes `SUBSCRIBERS_HIDDEN` / `ENGAGEMENT_UNAVAILABLE` | `filter_results` |
| robots.txt disallow / unreachable / 4xx / non-HTML | robotparser / status | Site skipped; email stays `Not Found` | `contacts.notes` |
| Email domain without mail server, or template domain | `email-validator` DNS / junk list | Candidate rejected with reason | `contacts.notes` |
| LLM 429 / 503 / timeout | SDK error | Backoff ×3, circuit breaker, next model in chain | logs; `pipeline_errors` if all fail |
| LLM invalid JSON or schema | Pydantic | Retried as a transient failure | — |
| LLM omits a creator from a batch | id check | Creator recorded and retried next run | `pipeline_errors` |
| Draft fails validators | `find_issues` | Rewrite with feedback ×2, then `NEEDS_REVIEW` | `outreach_messages.validation_issues` |
| Reviewer submits an empty field | schema | Clear `ReviewError`; nothing saved | UI message |
| SMTP auth / send error | smtplib / n8n error output | `FAILED` + error; retried up to 3 attempts | `outreach`, `outreach_events` |
| Sender crash mid-batch | stale `SENDING` | Released after 10 minutes | `outreach_events` |
| Duplicate result report | state check | 409 / `InvalidTransitionError` | — |
| Ctrl+C or crash during a run | `BaseException` handler | Run marked `FAILED` with the cause; finished work kept | `runs` |
| Missing secret | `require()` at point of use | Fails fast with the variable name and `docs/SETUP.md` pointer | stderr |

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
