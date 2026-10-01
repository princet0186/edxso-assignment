# Delivery Phases

The build is split into **4 phases**. Each phase ends with a working, demonstrable increment, a docs update and a checkpoint commit. Requirement IDs refer to [PRD.md](PRD.md); section references (§) refer to [EPS.md](EPS.md).

| Phase | Name | EPS milestones | Est. | Needs from you before starting |
|---|---|---|---|---|
| 1 | Foundation & Discovery | M0, M1 | 1.5 days | `uv` installed, `YOUTUBE_API_KEY` |
| 2 | Intelligence: Classify, Filter, Enrich | M2, M3 | 2 days | `GEMINI_API_KEY`, `GROQ_API_KEY` |
| 3 | AI Personalization & Review | M4, M6 (review page) | 1.5 days | Brand decision (PRD OQ-1) |
| 4 | Delivery, Automation & Submission | M5, M6 (tracker/DM), M7 | 2 days | Gmail app password, `TEST_INBOX`, n8n running |

**Rules that apply to every phase:**
- At the end of each phase, update the status in this file, add DECISIONS.md entries for any choices made, and update the EPS if the design changed.
- No phase starts until the previous phase's exit criteria pass.
- Secrets live in `config/.env` only. They are never committed and never pasted into chat.

---

## Phase 1 — Foundation & Discovery
**Goal:** a reproducible project skeleton that pulls ≥ 200 real YouTube candidates with computed metrics into SQLite.

### Tasks
- [ ] `pyproject.toml` (Python 3.12 pinned through uv), dependencies, ruff/pytest config
- [ ] `config.py`: `.env` plus `settings.yaml` loading with pydantic-settings; fails fast on a missing key
- [ ] `config/settings.yaml`: niche, ~30 discovery queries, thresholds, weights (EPS §4)
- [ ] `models.py` + `db.py`: all tables from EPS §5 with their UNIQUE constraints; `outreach init-db`
- [ ] `sources/youtube.py`: REST client, `QuotaTracker`, retries/backoff, quota-exceeded handling
- [ ] `discovery.py`: search → dedupe → `channels.list` → subscriber pre-filter → uploads → `videos.list` (EPS §6.1)
- [ ] `metrics.py`: ER (median, hidden likes, shorts fallback), views/sub, activity (EPS §6.2)
- [ ] `cli.py`: `init-db`, `run --stages discover,metrics`, `summary`
- [ ] Recorded YouTube JSON fixtures in `tests/fixtures/`; unit tests for ER and quota accounting

### Deliverables
Working `outreach run --stages discover,metrics`, a populated `data/outreach.db`, and the run summary with the quota used.

### Exit criteria
- [ ] ≥ 200 unique candidate channels in `creators`
- [ ] Every channel inside the subscriber range has metrics or an explicit reason why not
- [ ] Re-running adds no duplicates and stays within the quota budget
- [ ] `pytest` passes offline

### Decisions expected
Exact keyword list; long-form vs. Shorts cut-off; quota ceiling.

---

## Phase 2 — Intelligence: Classify, Filter, Enrich
**Goal:** every creator is `QUALIFIED` or `REJECTED` with all applicable reasons, and every qualified creator has a verified email or `Not Found`.

### Tasks
- [ ] `llm/client.py`: OpenAI-compatible multi-provider client, token-bucket rate limiter, fallback chain, `llm_cache` (EPS §6.7)
- [ ] Pick and record the free models (query each provider's `/models`, run a short JSON-output test) → DECISIONS entry
- [ ] `prompts/classify_v1.md` + `classify.py` with schema validation and deterministic brand-safety keywords (EPS §6.3)
- [ ] `filtering/rules.py`: all reason codes from EPS §6.4; evaluates every rule; reason text
- [ ] `filtering/scoring.py`: brand-fit score 0–100
- [ ] `enrichment/emails.py`: regex, de-obfuscation, junk filter, `email-validator`, MX lookup
- [ ] `enrichment/website.py`: robots-aware fetch of home, contact and about pages (≤ 4 pages, 1 request/sec per domain)
- [ ] `enrichment/socials.py`: Instagram, TikTok, X, LinkedIn and website from links
- [ ] Re-score after enrichment (contactability term)
- [ ] Unit tests: each rule, scoring, email extraction (including junk cases), LLM fallback with a fake provider

### Deliverables
`outreach run --stages classify,filter,enrich`; a first draft of `outputs/influencers.csv`.

### Exit criteria
- [ ] ≥ 50 records with complete metrics; each one QUALIFIED/REJECTED with reasons
- [ ] ≥ 20 qualified (or the thresholds are revisited and the change recorded in DECISIONS)
- [ ] 0 guessed emails; every found email has a source URL; the hit rate is reported
- [ ] A rate-limit (429) from the first provider triggers fallback without crashing

### Decisions expected
Model IDs; final ER and relevance thresholds after seeing the real distribution; geography/language (PRD OQ-3).

---

## Phase 3 — AI Personalization & Review
**Goal:** a validated, clearly personalized email and DM for every qualified creator, and a review UI to approve them.

### Tasks
- [ ] `config/brand.yaml`: brand persona, product, value propositions, signature, opt-out line
- [ ] `personalize/angle.py`: rule table from EPS §6.6
- [ ] `prompts/outreach_v1.md`: system prompt, creator brief, brand brief, constraints, 2 fictional style examples
- [ ] `personalize/generator.py`: generate → validate → re-prompt with feedback (×2) → `NEEDS_REVIEW`
- [ ] `personalize/validators.py`: word counts, name present, referenced title exists, placeholders, clichés, invented numbers
- [ ] `personalize/similarity.py`: trigram Jaccard across emails; flags above 0.5
- [ ] `app/streamlit_app.py`: Dashboard, Creators and **Review queue** pages (approve, edit + re-validate, reject)
- [ ] `outreach approve --all-valid` demo helper
- [ ] Unit tests for every validator and the angle rules

### Deliverables
`outreach run --stages generate`; `outputs/messages.csv`; review UI running with `uv run streamlit run app/streamlit_app.py`.

### Exit criteria
- [ ] ≥ 95% of messages pass validation on the first attempt or a retry
- [ ] No pair of emails above the similarity threshold (or each flagged pair is reviewed)
- [ ] Spot-check of 10 messages: each mentions a real, specific recent video and a fitting angle
- [ ] Approve and reject actions persist to the DB

### Decisions expected
Brand persona (OQ-1); tone guidelines; whether to try `outreach_v2` after spot-checking.

---

## Phase 4 — Delivery, Automation & Submission
**Goal:** approval-gated sending through n8n with zero duplicates, a full tracker, and a submission package a reviewer can run.

### Tasks
- [ ] `sending/claim.py`: atomic claim, stale-claim release, `SEND_MODE` handling (EPS §9.1)
- [ ] `sending/smtp.py` + `outreach send` CLI fallback
- [ ] `sending/dm_queue.py` + Streamlit **DM queue** page (copy, open profile, mark sent manually)
- [ ] `api/main.py`: FastAPI endpoints from EPS §8, `X-API-Key` auth, async stage jobs
- [ ] n8n workflows: WF1 `pipeline_run`, WF2 `outreach_send`, WF3 `error_handler`, exported to `n8n/workflows/` with credentials stripped
- [ ] Streamlit **Tracker** page and event log
- [ ] `exports.py`: influencers, messages, tracker (CSV + XLSX) and `run_summary.json`
- [ ] Sending tests: double claim → no duplicates; `SENT` never re-claimed; REDIRECT rewrite; LIVE without allowlist → `SKIPPED`
- [ ] Final full run of the pipeline
- [ ] README: stack, APIs, data sources, methodology, filtering, enrichment, prompts, personalization, sending, limitations, setup (all 11 items the brief requires)
- [ ] Screenshots (n8n canvas, Streamlit pages, test inbox) and a 3–5 minute demo video
- [ ] GitHub repository published

### Deliverables
Everything listed in assignment §10 (Submission Requirements).

### Exit criteria
- [ ] Running WF2 twice → 0 duplicate outreach rows or sends
- [ ] A REDIRECT-mode email actually arrives in `TEST_INBOX`
- [ ] The full pipeline runs end to end through **both** the CLI and n8n WF1
- [ ] A fresh clone followed by the README works in ≤ 15 minutes (CLI path)
- [ ] Every row of the assignment's §9 evaluation criteria can be pointed to in the repo

### Decisions expected
Send throttle and batch size; what goes in the demo video.

---

## Status

| Phase | Status | Started | Completed | Notes |
|---|---|---|---|---|
| 1 | Not started | | | Waiting on `uv` + YouTube key |
| 2 | Not started | | | |
| 3 | Not started | | | |
| 4 | Not started | | | |
