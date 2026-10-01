# PRD — Automated Micro-Influencer Outreach System

| | |
|---|---|
| **Project** | EDXSO AI Engineer Intern — Assignment 1 |
| **Document** | Product Requirements Document (PRD) |
| **Version** | 1.0 (baseline) |
| **Date** | 2026-10-01 |
| **Status** | Approved for build |
| **Companion docs** | [EPS.md](EPS.md) (how it is built) · [DECISIONS.md](DECISIONS.md) (why) |

---

## 1. Summary

Build a **zero-cost, working pipeline** that discovers Tech/EdTech micro-influencers on YouTube, filters and classifies them using clear rules, adds verified contact data and content context to each profile, generates personalized outreach (an email pitch and an Instagram DM) with a free-tier LLM, and delivers it through an **n8n-orchestrated sending layer**. That layer prevents duplicate outreach and keeps an outreach log.

The system must use **real data only**. Any value we cannot obtain is marked `Not Found` / `Not Available`, never guessed.

## 2. Problem & Goal

Brands find micro-influencers by hand, which is slow. Recruiters copy follower counts out of profiles and send generic "we'd love to collaborate" messages that get ignored. The goal is to show that this whole workflow can be automated end to end with sound engineering:

**Discovery → Data Collection → Filtering → Enrichment → AI Personalization → Review → Sending → Tracking**

## 3. Scope

### In scope (v1)
- **Platform:** YouTube, through the official YouTube Data API v3.
- **Niche:** Technology & EdTech (coding, AI/ML, data science, dev tools, tech careers, exam/skill learning).
- **Discovery:** at least 200 candidate channels found with keyword search, so that at least 50 complete records land in the dataset.
- **Filtering:** one complete filtering category (Tech & EdTech) with rules and a brand-fit score; every pass or fail comes with reasons.
- **Enrichment:** all mandatory fields; optional fields wherever public data allows.
- **Personalization:** an email (60–90 words) and an Instagram DM (15–30 words) for every qualified creator, generated individually and checked automatically.
- **Review:** human approval step in a Streamlit UI before anything is sent.
- **Sending:** an n8n workflow with **dry-run** and **redirect-to-test-inbox** modes over Gmail SMTP; duplicate prevention; outreach log.
- **Instagram DMs:** generated and shown, then sent by hand from a manual queue (no automation of Instagram).
- **Deliverables:** dataset (CSV/XLSX), messages export, tracker export, README, exported n8n workflows, demo video and screenshots.

### Out of scope (v1)
- Instagram/TikTok discovery or scraping; automated Instagram DMs.
- Sending to real creators (the `LIVE` mode exists in code but is disabled and not used in the demo).
- Bypassing YouTube's captcha-protected "View email address" button.
- Audience demographics from private analytics (only the channel owner can see them).
- Any paid API, paid tier or paid hosting.

## 4. Users & Personas

| Persona | Need | How the system serves them |
|---|---|---|
| **Campaign manager** (primary) | A shortlist of relevant creators with contacts and ready-to-send pitches | Runs the pipeline, reviews and approves messages, watches the tracker |
| **Reviewer / evaluator** (EDXSO) | To check that the system works, the data is real and the code is maintainable | Reads the README, runs the CLI or n8n, inspects the exports and logs |
| **Developer** (future) | To extend to more niches, platforms or 500+ creators | Modular code, config-driven rules, documented contracts in the EPS |

## 5. Constraints

| ID | Constraint |
|---|---|
| C-1 | **Zero cost.** Only free tiers and free, self-hosted tools. |
| C-2 | **No fabrication.** No made-up influencers, emails, metrics or "recent content". Unavailable data is labelled explicitly. |
| C-3 | **Platform compliance.** Official APIs where they exist; respect `robots.txt`; no captcha bypass; no Instagram automation. |
| C-4 | **Safe sending.** Real emails go only to the developer's own test inbox during the demo. |
| C-5 | **Free-tier LLM limits.** The pipeline must stay within rate limits (throttling, retries, caching, provider fallback). |

## 6. Functional Requirements

Priority: **P0** = required by the assignment · **P1** = strongly improves the evaluation score · **P2** = nice to have.

### 6.1 Discovery (FR-D)
| ID | Requirement | Priority |
|---|---|---|
| FR-D1 | Search YouTube with a configurable list of niche keyword queries and collect unique channel IDs. | P0 |
| FR-D2 | Fetch channel metadata and stats (name, handle, URL, subscribers, total videos, country, description). | P0 |
| FR-D3 | Fetch the most recent uploads (default N=10) with views, likes, comments, duration and publish date. | P0 |
| FR-D4 | Discover ≥ 200 candidates per run so that ≥ 50 complete records reach the dataset. | P0 |
| FR-D5 | Track YouTube API quota use per run and stop cleanly before the daily limit. | P1 |
| FR-D6 | Runs are incremental: creators that were already discovered are refreshed, not duplicated. | P1 |

### 6.2 Filtering & Classification (FR-F)
| ID | Requirement | Priority |
|---|---|---|
| FR-F1 | Hard filter: subscribers between 5,000 and 100,000 (inclusive; configurable). | P0 |
| FR-F2 | Hard filter: engagement rate ≥ threshold (default 2.0%; definition in EPS §6). | P0 |
| FR-F3 | Hard filter: active channel (≥ 1 upload in the last 90 days). | P0 |
| FR-F4 | LLM classification: primary niche, sub-niches, content themes, tone, audience level, language, brand-safety flags, relevance score (0–1) with evidence. | P0 |
| FR-F5 | Hard filter: niche is Tech/EdTech with relevance ≥ 0.6, and there are no brand-safety flags. | P0 |
| FR-F6 | Record geography (channel country) and platform on every record; geography can optionally be filtered through config. | P0 |
| FR-F7 | Every creator gets `QUALIFIED` or `REJECTED` plus **all** reasons that applied (codes + readable text). | P0 |
| FR-F8 | Brand-fit score (0–100) ranks the qualified creators. | P1 |
| FR-F9 | Audience demographics are recorded as `Not Available` (they need the creator's own analytics); audience level is labelled as *inferred*. | P0 |

### 6.3 Profile Enrichment (FR-E)
| ID | Requirement | Priority |
|---|---|---|
| FR-E1 | All mandatory fields are filled for every shortlisted creator: name, platform, profile URL, followers, engagement rate, niche, content themes, contact email. | P0 |
| FR-E2 | Email extraction, in order: channel description → recent video descriptions → the creator's own linked website (home, contact and about pages). | P0 |
| FR-E3 | Emails are validated (syntax, placeholder/junk filter, domain MX record). The source URL of each email is stored. | P0 |
| FR-E4 | If no valid email is found, the value is exactly `Not Found`. Emails are never guessed or built from patterns. | P0 |
| FR-E5 | Extract optional links: Instagram, TikTok, X, LinkedIn and website. | P1 |
| FR-E6 | Optional fields with no data (age, gender, geography) are marked `Not Available`. | P0 |

### 6.4 AI Personalization (FR-P)
| ID | Requirement | Priority |
|---|---|---|
| FR-P1 | Generate an email pitch (subject + body of 60–90 words) and an Instagram DM (15–30 words) for every qualified creator. | P0 |
| FR-P2 | Messages use real signals: creator name, niche, tone, a specific recent video, audience, the proposed collaboration and the value proposition. | P0 |
| FR-P3 | The collaboration angle (sponsorship, affiliate, UGC, ambassador, paid placement, barter) is chosen per creator by explainable rules. | P1 |
| FR-P4 | Automatic validation: word counts, no unfilled placeholders, at least 2 personalization signals, no invented facts (any video it references must exist), banned clichés. | P0 |
| FR-P5 | When validation fails, regenerate with the validator's feedback (up to 2 retries), then mark `NEEDS_REVIEW`. | P1 |
| FR-P6 | Similarity check across all messages flags any that look templated. | P1 |
| FR-P7 | Store the model, provider and prompt version with every message. | P1 |

### 6.5 Review (FR-R)
| ID | Requirement | Priority |
|---|---|---|
| FR-R1 | Streamlit UI lists generated messages with the signals they used; the reviewer can approve, edit or reject each one. | P1 |
| FR-R2 | Only `APPROVED` messages can be sent. A config flag (`AUTO_APPROVE`) exists for unattended demos. | P0 |

### 6.6 Sending & Tracking (FR-S)
| ID | Requirement | Priority |
|---|---|---|
| FR-S1 | Select only creators with a valid email and an approved message. | P0 |
| FR-S2 | Retrieve that creator's personalized email. | P0 |
| FR-S3 | Send or simulate the email. Modes: `DRY_RUN` (no SMTP), `REDIRECT` (real SMTP to the test inbox), `LIVE` (disabled). | P0 |
| FR-S4 | Record the status of every attempt (`SIMULATED`, `SENT`, `FAILED`, `SKIPPED`) with a timestamp and error detail. | P0 |
| FR-S5 | Prevent duplicate outreach: one outreach per creator, campaign and channel, enforced by **database constraints** and an atomic claim (not only by workflow logic). | P0 |
| FR-S6 | Keep an append-only outreach event log. | P0 |
| FR-S7 | n8n orchestrates the pipeline stages and the sending loop; the workflows are exported as JSON into the repo. | P1 |
| FR-S8 | A Python CLI sends through the same claim/log logic, so the system runs without n8n. | P1 |
| FR-S9 | Instagram DM manual queue: show the DM, open the creator's Instagram (if a handle was found), and mark it "sent manually" with a timestamp. | P0 |

### 6.7 Outputs (FR-X)
| ID | Requirement | Priority |
|---|---|---|
| FR-X1 | Dataset export (≥ 50 rows): Name, Platform, Followers, Engagement, Niche, Email, Profile URL, Content Theme, Status, plus reasons. | P0 |
| FR-X2 | Messages export: email subject and body, DM, word counts, signals used. | P0 |
| FR-X3 | Tracker export: Influencer, Email, Message Generated, Sent, Date, Status. | P0 |
| FR-X4 | Run summary: counts per stage, email hit rate, rejection reasons, LLM calls, quota used. | P1 |

## 7. Non-Functional Requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-1 | Cost | ₹0 / $0 to build, run and demo. |
| NFR-2 | Reliability | Each stage can be re-run safely and resumes where it stopped; one creator failing never stops the run. |
| NFR-3 | Error handling | Retries with backoff for HTTP and LLM calls; every error is recorded per creator with a reason. |
| NFR-4 | Scalability | Getting from 50 to 500+ creators needs only config and quota changes, no code rewrite. |
| NFR-5 | Maintainability | Modules with typed interfaces (Pydantic); config in YAML; prompts versioned as files; tests for rules and validators. |
| NFR-6 | Reproducibility | One-command setup (`uv sync`) plus `.env.example`; n8n workflows importable from JSON. |
| NFR-7 | Security | Secrets only in `.env` (git-ignored); API calls between n8n and the backend authenticated with a shared key. |
| NFR-8 | Transparency | Every value can be traced to a source (API field, URL or LLM output plus prompt version). |
| NFR-9 | Performance | A full run of ~200 candidates finishes in under 30 minutes on free tiers. |

## 8. Success Metrics (definition of done)

| Metric | Target |
|---|---|
| Records in dataset (with complete metrics) | ≥ 50 |
| Qualified creators | ≥ 20 |
| Qualified creators with a valid email | ≥ 15 (the email hit rate is reported honestly whatever it turns out to be) |
| Messages passing automatic validation on the first or a retried attempt | ≥ 95% |
| Duplicate sends after running the send workflow twice | 0 |
| Fabricated values | 0 |
| Fresh-clone setup time for a reviewer (CLI path) | ≤ 15 minutes |

## 9. Mapping to Evaluation Criteria

| # | Criterion | How this PRD addresses it |
|---|---|---|
| 1 | Functionality | Full pipeline in the CLI and in n8n (FR-D through FR-S) |
| 2 | Data quality | Official API metrics, validated emails with their sources, `Not Found` labels (C-2, FR-E3/E4) |
| 3 | Automation | n8n orchestration, scheduled send loop, auto-retry; manual steps limited to review and Instagram DMs (FR-S7) |
| 4 | Filtering logic | Explicit rules, reason codes, LLM classification with evidence, brand-fit score (FR-F) |
| 5 | Profile enrichment | Multi-source email extraction, socials, themes, geography (FR-E) |
| 6 | AI personalization | Signal-grounded prompts, rule-chosen angle, validators, similarity check (FR-P) |
| 7 | Engineering quality | Modular package, typed contracts, tests, versioned prompts (NFR-5) |
| 8 | Error handling | Per-creator error isolation, retries, provider fallback (NFR-2/3) |
| 9 | Documentation | PRD, EPS, decision log, README (FR-X, NFR-6) |
| 10 | Scalability | Incremental runs, quota tracker, Postgres-ready ORM, n8n queue mode (NFR-4) |

## 10. Assumptions

- A-1: "Followers" for YouTube means **subscribers**.
- A-2: The brand being pitched is configured in `config/brand.yaml`. The default is a clearly labelled **demo EdTech brand**, so we don't invent claims about any real company's products (see OQ-1).
- A-3: The 60–90 word count covers the email **body** (greeting through sign-off). The subject line and a fixed signature/opt-out footer are not counted.
- A-4: The free-tier limits cited in DECISIONS.md are correct as of 2026-10-01. Model names stay in config because free model lists change.

## 11. Open Questions

| ID | Question | Default if unanswered |
|---|---|---|
| OQ-1 | Should the pitch use EDXSO's real product and value proposition? If so, which ones? | Demo brand in `brand.yaml` |
| OQ-2 | Submission deadline? | Plan for about 7 working days (EPS §14) |
| OQ-3 | Target geography or language (e.g., India, English and Hinglish)? | Global; country recorded but not filtered; English and Hinglish allowed |

## 12. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Low email hit rate on YouTube | Fewer people to send to | Larger candidate pool (≥ 200), website crawl, honest reporting |
| Free-tier LLM rate limits or model removal | Generation stalls | Rate limiter, caching, Gemini → Groq fallback, batched classification, models in config |
| YouTube quota exhaustion | Discovery stops | Quota budget per run, pre-filter before fetching videos, incremental runs |
| LLM invents "recent content" | Personalization looks fake | Only real titles go into the prompt; validator checks any referenced title exists |
| Accidental real sends | Spam, reputation damage | Default `DRY_RUN`; `REDIRECT` rewrites the recipient; `LIVE` needs an explicit flag and an allowlist |
| A reviewer can't run n8n | Automation not seen | CLI path works the same way; exported workflows, screenshots and video |
