# Automated Micro-Influencer Outreach System

![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)

An end-to-end, **zero-cost** pipeline that discovers Tech/EdTech micro-influencers on YouTube, measures their real engagement, qualifies them with explainable rules, finds verified contact emails, writes a personalized email and Instagram DM for each with a free-tier LLM, and delivers outreach through **n8n**. The database guarantees that no creator is contacted twice.

**Principle: real data only.** Metrics come from the official YouTube API. Emails are *found*, never guessed. Anything unavailable is labelled `Not Found` / `Not Available`. The LLM may only state facts from its brief, and a validator enforces that.

| | |
|---|---|
| **Planning** | [PRD](docs/PRD.md) · [Engineering spec (EPS)](docs/EPS.md) · [Decision log (24 decisions)](docs/DECISIONS.md) · [Delivery phases](docs/PHASES.md) · [External setup](docs/SETUP.md) |
| **Deliverables** | [`outputs/`](outputs/): dataset, messages, tracker, run summary · [`n8n/workflows/`](n8n/workflows/): automation workflows |
| **Quality** | 114 offline tests · ruff lint · GitHub Actions CI |

---

## Results of the real run

From one real run on 2026-10-01/02 using only free tiers. Full numbers: [`outputs/run_summary.json`](outputs/run_summary.json).

| Funnel step | Count |
|---|---|
| Channels discovered (30 queries, 3,558 of 10,000 YouTube quota units) | **1,049** |
| Within 5k–100k subscribers (micro-influencer range) | **263** |
| Real engagement rate computed (27 honestly `Not Available`: likes hidden or too few videos) | **236** |
| Niche-classified by the LLM | **263** |
| **Qualified** (Technology & EdTech) | **93** |
| Verified contact email found (47.3% hit rate: 33 channel description, 9 video descriptions, 2 own website) | **44** |
| Personalized email + DM generated | see `run_summary.json` → `funnel` |

**Why the 170 in-range creators were rejected** (a creator can have several reasons): engagement below 2% (125), engagement not measurable (27), relevance below 0.6 (23), niche mismatch (14), language (10), brand safety (2).

**Message quality** (first 57 generated):
- **100% passed every validator**, 65% on the first attempt and the rest after feedback rewrites.
- Emails are 65–90 words and DMs 24–30.
- **0** were flagged as templated.
- All six collaboration angles were used.

| File | What it is |
|---|---|
| [`outputs/influencers.csv`](outputs/influencers.csv) | 263 micro-influencer candidates: metrics, niche, themes, email (+ source), socials, status, score, reasons |
| [`outputs/discovered_channels.csv`](outputs/discovered_channels.csv) | All 1,049 channels discovered, with status and reason |
| [`outputs/messages.csv`](outputs/messages.csv) | Email pitch + Instagram DM per qualified creator, with angle, signals, validation and model |
| [`outputs/outreach_tracker.csv`](outputs/outreach_tracker.csv) | Influencer · Email · Message Generated · Sent · Date · Status (+ mode, attempts, DM status) |
| [`outputs/outreach_results.xlsx`](outputs/outreach_results.xlsx) | All of the above as sheets |

---

## Architecture

```
                      n8n (self-hosted)                          Streamlit console
          WF1 pipeline_run · WF2 outreach_send · WF3 errors    review · DMs · tracker
                         │ HTTP + X-API-Key                             │
                         ▼                                              ▼
 CLI ──▶  ┌──────────────────── Python core (src/outreach) ────────────────────┐
          │ discover → metrics → classify → filter → enrich → generate → export │
          │                review → queue (atomic claim) → send/simulate → log  │
          └───┬───────────────┬────────────────┬────────────────┬──────────────┘
       YouTube Data API   creators' own     Gemini → Groq       SQLite (source of truth)
       v3 (official)      websites (robots)  (free tiers)       → outputs/*.csv · .xlsx
```

All business rules live in the Python package. The CLI, the FastAPI service used by n8n, and the Streamlit console are thin layers over the same functions. The system therefore behaves identically with or without n8n.

## 1. Technology stack

| Concern | Choice |
|---|---|
| Language / packaging | Python 3.12, `uv` (lockfile) |
| Storage | SQLite via SQLModel (WAL mode); one `DATABASE_URL` change to move to Postgres |
| Validation | Pydantic v2, pydantic-settings |
| HTTP / retries | httpx, tenacity |
| LLM access | `openai` SDK against OpenAI-compatible endpoints (Gemini, Groq) |
| Scraping / email checks | BeautifulSoup, `email-validator` (DNS MX lookup) |
| API / CLI / UI | FastAPI, Typer, Streamlit |
| Orchestration & sending | n8n Community Edition (self-hosted) + Gmail SMTP |
| Quality | pytest, ruff, GitHub Actions |

## 2. APIs and tools used (all free)

| API / tool | Used for | Limit handling |
|---|---|---|
| YouTube Data API v3 | Search, channel stats, recent videos | Unit-cost accounting per call, 8,000-unit daily ceiling counted from Pacific midnight, query log so searches never repeat |
| Google Gemini (AI Studio free tier) | Classification and message writing | Per-model pacing, retries, circuit breaker, fallback |
| Groq (free tier) | LLM fallback (`openai/gpt-oss-120b`) | Last in the chain because of its 8K tokens/minute cap |
| Gmail SMTP (App Password) | Real email delivery in `REDIRECT` / `LIVE` mode | Batches of 10, spaced sends |
| n8n | Pipeline orchestration, approval-gated sending loop, error routing | — |
| DNS (via `email-validator`) | Verifying an email domain can receive mail | Cached resolver |

## 3. Data sources
- **YouTube Data API v3**, the only source of creators and metrics: `search.list` (recent videos per query), `channels.list` (subscribers, country, description, topics), `playlistItems.list` + `videos.list` (last 10 uploads with views, likes, comments, duration).
- **Creators' own public pages**: the channel description, video descriptions, and the website linked from the channel description (respecting `robots.txt`). Used for email and social links only.
- Not used, deliberately: Instagram/TikTok scraping, YouTube's captcha-protected "View email address" button, paid databases. See [D-1](docs/DECISIONS.md) and [D-8](docs/DECISIONS.md).

## 4. Discovery methodology
1. 30 Tech/EdTech search queries ([`config/settings.yaml`](config/settings.yaml)), e.g. "python tutorial for beginners" and "coding interview preparation". Each searches **videos published in the last 90 days**, which surfaces creators who are active now.
2. Search ordering is `relevance`, chosen by experiment: `viewCount` returned 29 of 35 channels above 100k subscribers ([D-16](docs/DECISIONS.md)).
3. Every new channel is stored. Recent videos are fetched **only** for channels within 5,000–100,000 subscribers, which spends quota on real candidates only.
4. Runs are incremental and resumable: searched queries are logged, channels are unique by ID, and a quota stop or crash resumes where it left off.

## 5. Filtering logic: Technology & EdTech category
Every rule is evaluated and **every** failure is reported with a reason ([`filtering/rules.py`](src/outreach/filtering/rules.py)):

| Rule | Threshold | Example reason |
|---|---|---|
| Followers | 5,000–100,000 subscribers (hidden = reject) | `142,300 subscribers is outside 5,000–100,000` |
| Activity | Upload in the last 90 days | `Last upload 2026-03-02 (213 days ago)` |
| Engagement rate | ≥ 2.0%, see definition below | `Engagement 1.10% < 2.00% minimum` |
| Niche | LLM-classified as Technology or EdTech | `Classified as Gaming` |
| Content relevance | Relevance ≥ 0.6 for a learning-product campaign | `Relevance 0.42 < 0.60` |
| Brand safety | No piracy, gambling, account hacking or scam content (LLM **and** a phrase list) | `Brand-safety flags: piracy` |
| Language | English or Hindi-English | `Content language: other` |
| Geography | Recorded for every creator; filtered only if an allowlist is set | `Country DE not in allowlist` |

**Engagement rate** = median over the last 10 settled uploads of (likes + comments) / views. Videos under 48 hours old are excluded. Long-form videos are preferred because Shorts inflate like-rates. The median is used so one viral video can't distort it. Hidden likes mean `Not Available`; the rate is never estimated.

Qualified creators are ranked by a **brand-fit score (0–100)**, and each score's breakdown is stored:
- relevance 35
- engagement 25
- size fit 15 (best at 10k–50k)
- activity 15
- verified email 10

## 6. Enrichment process
For each qualified creator ([`enrichment/`](src/outreach/enrichment/)):
1. **Email**, tried in reliability order:
   - **channel description** (business-labelled addresses first)
   - **video descriptions** (only addresses repeated across uploads, or labelled "business/collab/email")
   - the creator's **own website**: robots-aware, at most 4 pages, follows contact/about links, prefers `mailto:`
2. Every address is **validated**: syntax, a junk filter (placeholder and template domains, `noreply@`, `logo@2x.png`), and a **DNS MX lookup**. The source URL is stored. Rejected candidates are kept in notes with the reason.
3. Nothing valid → `Not Found`. Addresses are never constructed or guessed.
4. **Socials and website** come from the creator's links. The website is taken only from the channel description, because video descriptions carry sponsor links.
5. **Content themes, tone, audience level and language** come from the LLM classification, with the evidence titles stored.
6. **Audience age and gender** are `Not Available`: YouTube only shows them to the channel owner. Geography is the channel's declared country.

## 7. AI model and prompts
- **Model chain** (free tiers; [D-18](docs/DECISIONS.md)): `gemini-2.5-flash` → `gemini-3.1-flash-lite` → Groq `openai/gpt-oss-120b`.
  - Each model has its own pacing, 3 retries with exponential backoff, and a circuit breaker (opens after 2 failures; the cooldown doubles from 5 minutes up to 1 hour).
  - Replies are cached in SQLite.
- **Classification:** [`prompts/classify_v1.md`](prompts/classify_v1.md).
  - 5 creators per request, temperature 0.1.
  - Evidence-only labels; strict JSON schema (niche, sub-niches, themes, tone, audience level, language, relevance 0–1, brand-safety flags, evidence titles).
- **Outreach:** [`prompts/outreach_v1.md`](prompts/outreach_v1.md).
  - Temperature 0.7.
  - Structure rules, tone matching, banned clichés, and the assignment's own generic-versus-specific example as the anchor.
- The provider, model and prompt version are stored with every label and message.

## 8. Personalization logic
1. **The angle is chosen by explainable rules, not the LLM** ([`personalize/angle.py`](src/outreach/personalize/angle.py)). First match wins:
   1. brand ambassador (engagement ≥ 6% and ≥ 6 uploads in 90 days)
   2. sponsored integration (tutorial channel with ≥ 20k subscribers)
   3. paid placement (reviews or tools content)
   4. affiliate (student or beginner audience)
   5. UGC (under 15k subscribers)
   6. barter (fallback)

   The reason is shown to reviewers.
2. **The brief** gives the model only stored facts: the creator's name, niche, themes, tone, audience, **two real recent videos**, the brand brief and the chosen offer.
3. **Validators** ([`personalize/validators.py`](src/outreach/personalize/validators.py)) reject a draft unless:
   - the email body is 60–90 words and the DM 15–30
   - the creator is addressed by name
   - the referenced video **exists in the brief and is mentioned**
   - there are no placeholders or clichés
   - **no number appears that isn't in the brief** (invented stats)
   - at least 2 personalization signals are used
4. **A failed draft is rewritten with the validator's feedback**, at most twice, then held as `NEEDS_REVIEW`.
5. **A template check:** word-trigram Jaccard similarity across all emails flags any pair that reads as templated.
6. **Human review** in the console. Edits are re-validated, and a message with open issues can't be approved.

The brand is **SkillSprint**, a clearly fictional demo brand ([`config/brand.yaml`](config/brand.yaml)), so no real company is misrepresented. Replacing that file pitches a real brand.

## 9. Sending mechanism
- **Queue** ([`sending/queue.py`](src/outreach/sending/queue.py)): approved messages with a verified email are queued with `INSERT … ON CONFLICT DO NOTHING`.
  - `UNIQUE(campaign, channel, creator)` and `UNIQUE(campaign, channel, recipient)` make duplicate outreach **impossible at the database level**.
- **Atomic claim:** one `UPDATE … WHERE id IN (SELECT … LIMIT n) RETURNING id`. A row enters `SENDING` once, so the CLI and n8n can never send the same email. Stale claims are released after 10 minutes.
- **Modes** (`SEND_MODE`):
  - `DRY_RUN` (default): simulate and log.
  - `REDIRECT`: real Gmail send to **your test inbox**, with subject `[TEST → creator@…]`.
  - `LIVE`: only to `LIVE_ALLOWLIST`; everyone else is `SKIPPED`.
- **Logging:** each result is accepted only from `SENDING`, so a double report is rejected. Every claim, send, simulation, failure and skip is written to the append-only `outreach_events` log. Failures retry up to 3 attempts.
- **n8n workflows** ([`n8n/workflows/`](n8n/workflows/)):
  - **WF1** runs each stage as an API job and polls it.
  - **WF2**, every 5 minutes: claim → simulate, or Gmail SMTP with an error branch → report.
  - **WF3** records workflow errors.
- **Python sender** (`outreach send`) uses the identical queue.
- **Instagram DMs:** Instagram doesn't allow automated cold DMs, so the console's DM queue shows each DM with a copy button and the profile link, and records **Mark as sent** once.

## 10. Limitations
- **Email coverage is partial** (see results). Many creators keep business emails behind YouTube's captcha-protected button, which we deliberately don't bypass.
- **Audience demographics** (age, gender, location breakdown) aren't public, so they're labelled `Not Available`. Country is the channel's declared country.
- **YouTube only.** Instagram and TikTok discovery would need paid or terms-violating scrapers. The `SourceClient` design allows adding them.
- **Niche, language and relevance are LLM judgments.** Evidence titles are stored so they can be audited.
- **Free-tier LLMs are slow and sometimes overloaded.** We saw 503s, per-model daily quotas and one stalled request, all handled by retries, fallback and resumable stages, at the cost of wall-clock time.
- **Grounding vs. naturalness:** to prove a real video is referenced, the validator requires most of the title's words to appear in the email, so drafts often quote titles verbatim. A v2 could accept a paraphrased topic, checked by an LLM judge against the brief.
- `create_all` creates missing tables but doesn't migrate changed ones. A production version would use Alembic.
- SQLite fits one machine; Postgres plus `SKIP LOCKED` would be the next step for concurrent senders at scale.

## 11. Setup

**Prerequisites:** macOS/Linux, [`uv`](https://docs.astral.sh/uv/), Node 20+ (only for n8n). Every key is free; step-by-step instructions are in [docs/SETUP.md](docs/SETUP.md).

```bash
git clone <this repo> && cd <repo>
uv sync                                   # installs Python 3.12 + dependencies
cp config/.env.example config/.env        # then fill in the keys (SETUP.md)
uv run outreach check-env                 # shows which keys are set (never their values)
uv run pytest -q                          # 114 offline tests, no keys needed
```

### Run the pipeline (CLI)
```bash
uv run outreach run                       # discover → metrics → classify → filter → enrich → generate → export
uv run outreach run --stages discover --limit 2   # small trial (~200 quota units)
uv run outreach summary                   # funnel + recent runs
```
Every stage is resumable. If a run stops (quota, network, Ctrl+C), run the same command again.

### Review, send, export
```bash
uv run streamlit run app/streamlit_app.py # console: dashboard, creators, review queue, DMs, tracker
uv run outreach approve                   # or bulk-approve every draft that passed validation
uv run outreach send                      # DRY_RUN by default; set SEND_MODE=REDIRECT + TEST_INBOX for real test sends
uv run outreach export                    # writes outputs/
uv run outreach clear-simulated           # optional: clear DRY_RUN results to re-demo in REDIRECT mode
```

### Run through n8n
```bash
uv run outreach api                       # API on http://localhost:8000 (needs API_KEY in config/.env)
npx n8n                                   # n8n UI on http://localhost:5678
```
In n8n:
1. **Import** the three files in `n8n/workflows/` (Workflows → Import from file).
2. **Create credentials:**
   - *Header Auth* named **Outreach API key**: header `X-API-Key`, value = your `API_KEY`.
   - *SMTP* named **Gmail SMTP**: host `smtp.gmail.com`, port 587, your Gmail address and App Password.
3. In **WF2 → Config**, set `fromEmail`. In WF1 and WF2 → Settings, set **Error workflow** to WF3.
4. Run **WF1** to execute the pipeline, then approve messages in the console. Then run **WF2** (or activate it to run every 5 minutes).

## Repository layout
```
config/        settings.yaml (rules) · brand.yaml (demo brand) · .env.example
prompts/       versioned LLM prompts
src/outreach/  sources/ · discovery · metrics · llm/ · classify · filtering/ · enrichment/
               personalize/ · sending/ · pipeline · jobs · api/ · reporting · exports · cli
app/           Streamlit console
n8n/workflows/ WF1 pipeline_run · WF2 outreach_send · WF3 error_handler
outputs/       deliverables from the real run
tests/         offline test suite
docs/          PRD · EPS · DECISIONS · PHASES · SETUP
```
