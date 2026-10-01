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

---

## Sources (checked 2026-10-01)
- n8n self-hosting is free: [goodspeed.studio](https://goodspeed.studio/blog/is-n8n-free), [ssdnodes.com](https://www.ssdnodes.com/learn/is-n8n-free-community-vs-enterprise)
- Groq free tier: [tokenmix.ai](https://tokenmix.ai/blog/groq-free-tier-limits-2026), [klymentiev.com](https://klymentiev.com/blog/groq-pricing)
- Gemini free tier: [aipromptshub.co](https://aipromptshub.co/limits/gemini-rate-limits-2026), [pecollective.com](https://pecollective.com/tools/gemini-free-tier-guide/)