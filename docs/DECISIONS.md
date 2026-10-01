# Decision Log

Each significant decision is recorded here when it is made: the context, the options considered, the choice, and its consequences. New entries are appended, never rewritten. A reversed decision gets a new entry that supersedes the old one.

Format: `D-<n> · <title> · <date> · Status`

---

### D-1 · Discovery platform: YouTube only · 2026-10-01 · Accepted
- **Context:** The assignment requires real data (no fabricated metrics) and the project must cost nothing.
- **Options:**
  1. YouTube Data API v3. Official and free (10k units/day). Gives subscriber, view, like and comment counts and the channel country.
  2. Instagram. The official Graph API can't discover unknown creators (Business Discovery needs the username in advance), so we'd need paid third-party scrapers that sit in a ToS grey area.
  3. Both.
- **Decision:** YouTube only.
- **Consequences:** Accurate metrics that can be re-fetched, with no legal risk. Instagram handles are still collected from creators' own links, which supports the DM workflow. The multi-platform design is kept (a `SourceClient` protocol) so Instagram or TikTok can be added later.

### D-2 · Niche: Technology & EdTech · 2026-10-01 · Accepted
- **Context:** One complete filtering category is required. Email disclosure rates differ a lot between niches.
- **Options:** Tech/EdTech, Fitness, Fashion & Beauty, Fintech.
- **Decision:** Tech/EdTech.
- **Why:** Education and tech YouTubers often publish business emails in their descriptions, and the niche fits EDXSO's domain.
- **Consequences:** The keyword list and brand persona target learning and dev-tool products. Fashion & Beauty, the brief's example, depends heavily on Instagram, which conflicts with D-1.

### D-3 · LLM: free-tier, provider-agnostic, Gemini → Groq · 2026-10-01 · Accepted
- **Context:** No budget. Needs structured JSON output for about 300 calls per run.
- **Options:** Gemini (Google AI Studio free tier), Groq free tier, a local model through Ollama.
- **Decision:** A single OpenAI-compatible client with a config-driven fallback chain: **Gemini Flash (primary) → Groq (fallback)**.
- **Why Gemini first:** a much higher tokens-per-minute cap than Groq's free tier, which matters because prompts include video titles and descriptions. Good at JSON output.
- **Why Groq second:** very fast, with 30 requests/min, and run by a different company, so one provider's outage or quota doesn't stop the run.
- **Why not local:** a local model needs capable hardware, and reviewers couldn't reproduce the results easily.
- **Consequences:**
  - Model IDs live in `.env` (free model lists change).
  - A client-side rate limiter and response cache are required.
  - Gemini's free tier may use prompts to improve Google's products. That's acceptable because we only send public creator data.
- **Free-tier facts as of 2026-10-01** (re-check before the demo):
  - Gemini: Flash-class models only, roughly 10 RPM and 1,500 RPD, no card needed.
  - Groq: 30 RPM, about 6K TPM, per-model daily token caps, no card needed.

### D-4 · Orchestration: n8n as orchestrator + sending layer · 2026-10-01 · Accepted
- **Context:** The brief names n8n three times (sending options, technical expectations, "automation workflow" deliverable). "Automation" is one of the evaluation criteria.
- **Options:** n8n as orchestrator and sender; n8n for sending only; no n8n.
- **Decision:** n8n triggers the pipeline stages (through the FastAPI backend), runs the approval-gated send loop, sends over SMTP, branches between dry-run and real sends, and reports status back.
- **Guardrails:**
  - All business rules stay in Python, so n8n holds no logic that would need duplicating.
  - Duplicate prevention is enforced by the database, not by workflow logic.
  - A CLI path gives the same results without n8n, since reviewers may not install it.
- **Cost:** The n8n Community Edition is free to self-host with no execution limits (run locally; see D-13).

### D-5 · Sending mode: DRY_RUN default, REDIRECT to test inbox, LIVE disabled · 2026-10-01 · Accepted
- **Context:** Sending to real creators is outward-facing and can't be undone. The brief accepts "send or simulate".
- **Decision:** Three modes:
  - `DRY_RUN` (default): simulated, logged.
  - `REDIRECT`: real Gmail SMTP to the developer's test inbox, with the intended recipient shown in the subject.
  - `LIVE`: only to an explicit allowlist; not used in the demo.
- **Consequences:** Proves real delivery works without contacting creators. Emails still include a sender identity and an opt-out line, so the system would be ready for compliant live use.

### D-6 · Storage: SQLite + SQLModel; exports to CSV/XLSX; no Google Sheets mirror · 2026-10-01 · Accepted
- **Context:** We need structured storage, duplicate-prevention constraints and a path to scale.
- **Decision:** SQLite as the source of truth (UNIQUE constraints carry the dedupe guarantees). CSV/XLSX exports are the deliverables. A Google Sheets mirror was offered and declined, to avoid OAuth setup and extra credentials.
- **Consequences:** No setup needed. Moving to Postgres is a DSN change (EPS §15).

### D-7 · Engagement-rate definition · 2026-10-01 · Accepted
- **Decision:** ER = **median** of (likes + comments) / views over the last 10 eligible uploads.
  - Eligible means: not live, older than 48 hours, long-form preferred.
  - If likes are hidden on most videos, ER = `Not Available` and the creator is rejected with a reason. ER is never estimated.
  - The minimum ER to qualify is 2.0% (configurable).
- **Why:** Engagement per view is the standard measure on YouTube. Engagement per subscriber penalizes channels with old, inactive subscribers. The median resists one viral video skewing the number.

### D-8 · Email enrichment boundaries · 2026-10-01 · Accepted
- **Decision:**
  - Allowed sources: channel and video descriptions, and the creator's own linked websites (only where `robots.txt` allows, max 4 pages).
  - Every email is validated with a syntax check, a junk filter and a domain MX lookup, and its source URL is stored.
  - YouTube's captcha-protected business email is **not** accessed.
  - No email pattern guessing.
  - If nothing is found, the value is `Not Found`.
- **Consequences:** The email hit rate will be below 100% and is reported honestly. This is offset by discovering at least 200 candidates.

### D-9 · Personalization: rules choose the angle, the LLM writes, validators gate · 2026-10-01 · Accepted
- **Decision:**
  - Explainable rules choose the collaboration angle.
  - The LLM gets only real signals (actual recent titles).
  - Automatic validators check word counts, placeholders, the creator's name, that any referenced title exists, and that no numbers were invented.
  - A failed validation triggers a re-prompt with feedback (up to 2 times), then `NEEDS_REVIEW`.
  - A trigram similarity check flags templated output.
- **Why:** Directly targets the "genuinely personalized" criterion, and guards against hallucinated "recent content".

### D-10 · Human review gate before sending · 2026-10-01 · Accepted
- **Decision:** Only `APPROVED` messages can be sent. Review happens in Streamlit. An `AUTO_APPROVE` flag (validated messages only) exists for unattended demos.
- **Why:** The brief's workflow includes a "Review" step; it is also good practice for AI-written outreach.

### D-11 · Instagram DMs: generated + manual queue · 2026-10-01 · Accepted
- **Decision:** DMs are generated and shown in a Streamlit DM queue (copy → open profile → mark sent manually). No automation of Instagram.
- **Why:** The brief explicitly forbids bypassing platform restrictions, and Instagram's messaging APIs don't allow cold outreach to arbitrary users.

### D-12 · Documentation set · 2026-10-01 · Accepted
- **Decision:** `docs/PRD.md` (what and why), `docs/EPS.md` (how), and `docs/DECISIONS.md` (this log), all kept in the repo and updated in each milestone. The README is written last and links to all three.

### D-13 · Run n8n with `npx`, not Docker · 2026-10-01 · Accepted
- **Context:** Docker isn't installed on the development machine, and Node 22 is.
- **Options:** Install Docker Desktop (several GB) and use a compose file; or run `npx n8n` on Node.
- **Decision:** Use `npx n8n`.
- **Consequences:**
  - No Docker install needed.
  - n8n and FastAPI both run on `localhost`, so no `host.docker.internal` networking.
  - Reviewers can import the exported workflow JSON into any n8n install, Docker or not.

### D-14 · Python 3.12 pinned through uv · 2026-10-01 · Accepted
- **Context:** The system Python is 3.14. Some data and LLM libraries may not ship wheels for the newest Python yet.
- **Decision:** Pin `requires-python = ">=3.12,<3.13"` and let uv provide the interpreter.
- **Consequences:** Reproducible environments for reviewers. The system Python is never touched.

### D-15 · Four delivery phases with exit criteria · 2026-10-01 · Accepted
- **Decision:** The 8 EPS milestones are grouped into 4 phases ([PHASES.md](PHASES.md)): Foundation & Discovery → Intelligence → Personalization & Review → Delivery & Submission. Each phase must pass its exit criteria before the next starts. External setup is done just before the phase that needs it ([SETUP.md](SETUP.md)).

### D-16 · Search ordering: `relevance`, chosen from measured yield · 2026-10-01 · Accepted
- **Context:** Micro-influencers are a narrow band (5k–100k). YouTube search ordering decides which channels we even see.
- **Experiment** (same query, "python tutorial for beginners", last 90 days):

  | Order | Channels | 5k–100k | Mostly |
  |---|---|---|---|
  | `relevance` | 43 | ~17% | under 1k subscribers |
  | `viewCount` | 35 | 14% (5/35) | over 100k (29/35) |

- **Decision:** Keep `relevance` and widen coverage with 30 queries instead.
- **Result (full run):** 1,049 channels discovered → **263 in range** → 236 with a real engagement rate. About 3,660 of the 10,000 daily quota units used.
- **Consequence:** Out-of-range channels are stored but their videos are never fetched, so the 83% that miss the size band cost only 1 unit per 50 channels.

### D-17 · Concrete LLM models: `gemini-3.5-flash` → Groq `openai/gpt-oss-120b` · 2026-10-01 · Accepted
- **Context:** Model names change. On 2026-10-01 the keys were probed live with a JSON-mode request.
- **Findings:**
  - `gemini-flash-latest` returned **503 "high demand"**: a live example of why a fallback chain is needed.
  - `gemini-3.5-flash` and `gemini-2.5-flash` returned valid JSON in about 2–3 s.
  - Groq no longer serves Llama models. `openai/gpt-oss-120b` returned valid JSON in 0.6 s.
- **Decision:** Primary `gemini-3.5-flash` (newest working Flash model, for writing quality); fallback Groq `openai/gpt-oss-120b`. Both are set in `config/.env` and can be changed without code changes.

### D-18 · Resilient free-tier LLM use: circuit breaker + per-model fallback · 2026-10-01 · Accepted (supersedes the model choice in D-17)
- **Context:** The live classification run measured three different failure modes:
  - Gemini returned 503 "high demand" repeatedly; each one cost 3 backed-off attempts before Groq was tried.
  - `gemini-3.5-flash` then hit its free quota (429).
  - Every Groq model shares an **8,000 tokens/minute** cap. A 5-creator batch requests about 6,000 tokens, so Groq manages about one batch a minute.
- **Decision:**
  1. **Circuit breaker:** after 2 consecutive failures a model is skipped for 5 minutes, then tried again.
  2. **Fallback per model, not per provider:** Gemini's free limits are per model, so `GEMINI_MODELS` / `GROQ_MODELS` are ordered lists and each model is its own fallback step with its own pacing and breaker.
  3. **Default chain:** `gemini-2.5-flash` → `gemini-3.1-flash-lite` → Groq `openai/gpt-oss-120b`. Groq is last because of its token cap.
- **Result:** The remaining classifications finished with 0 failures. Successful answers are cached, so re-runs cost nothing.

### D-19 · Classify in batches, and only creators with real metrics · 2026-10-01 · Accepted
- **Context:** About 260 creators need niche labels, and free tiers allow few requests per minute.
- **Decision:**
  - Classify **5 creators per request**. Each reply is validated against a strict schema, and every requested channel ID must come back; a missing one is recorded and retried next run.
  - Classify only creators inside the size range with fetched videos. Out-of-range channels are rejected on size alone, so labelling them would waste quota.
- **Trade-off:** One malformed batch delays 5 creators instead of 1. That's acceptable because retries are automatic.

### D-20 · Email enrichment hardening found by spot-checking real data · 2026-10-01 · Accepted
- **Context:** The 45 emails found were reviewed by hand. One, `info@mysite.com`, was a website-template default: the domain exists and has MX records, so the DNS check alone accepted it.
- **Decision:**
  - Template domains (`mysite.com`, `yoursite.com`, `website.com`, …) are now treated as junk.
  - Each candidate address is checked only once.
  - The affected creator was re-enriched and is now honestly `Not Found`.
- **Standing rules (unchanged):**
  - A creator's website comes **only** from the channel description; video descriptions are full of sponsor links.
  - Addresses found in video descriptions must repeat across uploads or carry a business label.

### D-21 · Defaults for the open product questions · 2026-10-01 · Accepted
- **OQ-1 (brand):** A clearly fictional demo brand, **SkillSprint** (`config/brand.yaml`, `is_demo_brand: true`), so generated pitches never make claims about a real company's products. Replacing that one file pitches a real brand.
- **OQ-3 (region/language):** Global discovery. Country is recorded, not filtered. Content languages `en` and `hi-en` (Hinglish) are allowed, because the pitches are written in English.

### D-22 · API runs stages as background jobs that n8n polls · 2026-10-01 · Accepted
- **Context:** Discovery and LLM stages take minutes, longer than a sensible HTTP timeout.
- **Decision:**
  - `POST /runs/{id}/stages/{stage}` returns **202 with a job ID** immediately, and n8n polls `GET /jobs/{id}` every 10 seconds.
  - Only one job may be active at a time (409 otherwise), because stages share one SQLite writer and one YouTube quota.
  - The API refuses to run if `API_KEY` is unset (503) instead of serving unauthenticated requests.

### D-23 · Validators as the quality gate for AI text · 2026-10-01 · Accepted
- **Decision:** A draft is only sendable after automatic checks:
  - word limits
  - the creator is addressed by name
  - the referenced video exists in the brief **and** is actually mentioned
  - no placeholders or clichés
  - **no number that isn't in the brief** (catches invented stats)
- **What happens on failure:** the validator's feedback goes back to the model for a rewrite, at most twice; then the draft is held as `NEEDS_REVIEW`. A reviewer's edit is re-validated the same way, and a message with open issues cannot be approved.

### D-24 · n8n credentials via the credential store, not workflow JSON · 2026-10-01 · Accepted
- **Decision:**
  - The API key is sent through an n8n **Header Auth credential** (`X-API-Key`).
  - SMTP uses an n8n **SMTP credential**.
  - Both live in n8n's encrypted credential store. The exported workflow JSON references them only by name, so the repo contains no secrets.
  - The API base URL sits in one `Config` node per workflow.

---

## Sources (checked 2026-10-01)
- n8n self-hosting is free: [goodspeed.studio](https://goodspeed.studio/blog/is-n8n-free), [ssdnodes.com](https://www.ssdnodes.com/learn/is-n8n-free-community-vs-enterprise)
- Groq free tier: [tokenmix.ai](https://tokenmix.ai/blog/groq-free-tier-limits-2026), [klymentiev.com](https://klymentiev.com/blog/groq-pricing)
- Gemini free tier: [aipromptshub.co](https://aipromptshub.co/limits/gemini-rate-limits-2026), [pecollective.com](https://pecollective.com/tools/gemini-free-tier-guide/)