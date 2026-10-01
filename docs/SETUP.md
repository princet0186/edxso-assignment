# External Setup Guide

Everything here is **free**, and none of it needs a credit card. Total time is about 20 minutes. Console screens change from time to time; if a label below doesn't match, look for the closest equivalent.

> **Never paste keys into chat, issues or commits.** All secrets live in `config/.env`, which is git-ignored. Run `uv run outreach check-env` to confirm which keys are set; it never prints the values.

```bash
cp config/.env.example config/.env
```

| # | What | Needed by | `config/.env` keys | Time |
|---|---|---|---|---|
| 1 | uv (Python manager) | Phase 1 | — | 2 min |
| 2 | YouTube Data API key | Phase 1 | `YOUTUBE_DATA_API_KEY` | 5 min |
| 3 | Gemini API key | Phase 2 | `GEMINI_API_KEY` | 3 min |
| 4 | Groq API key | Phase 2 | `GROQ_API_KEY` | 2 min |
| 5 | Gmail sender + App Password | Phase 4 | `SMTP_USER`, `SMTP_APP_PASSWORD`, `SENDER_NAME` | 5 min |
| 6 | Test inbox | Phase 4 | `TEST_INBOX` | 1 min |
| 7 | Backend API secret | Phase 4 | `API_KEY` | 1 min |
| 8 | n8n (local) | Phase 4 | — | 3 min |
| 9 | GitHub repository | Submission | — | 2 min |

---

## 1. uv
```bash
brew install uv
uv --version
```
uv downloads Python 3.12 for this project by itself, so the system Python isn't touched.

## 2. YouTube Data API v3 key
1. Open <https://console.cloud.google.com/> and sign in.
2. Project picker (top bar) → **New Project** → name it `edxso-outreach` → Create, then select it.
3. **APIs & Services → Library** → search "YouTube Data API v3" → **Enable**.
4. **APIs & Services → Credentials → Create credentials → API key**.
5. Click the new key → **API restrictions → Restrict key** → tick only *YouTube Data API v3* → Save.
6. Paste the key into `config/.env` as `YOUTUBE_DATA_API_KEY`.

Notes:
- No billing is needed.
- The default quota is 10,000 units a day. It resets at midnight Pacific time.
- You can see usage under *APIs & Services → YouTube Data API v3 → Quotas*.

## 3. Gemini API key (Google AI Studio)
1. Open <https://aistudio.google.com/> and sign in.
2. **Get API key → Create API key** (choose the `edxso-outreach` project or let it create one).
3. Paste it as `GEMINI_API_KEY`.

**Do not enable billing on that project.** Turning on billing ends the free tier for it.

## 4. Groq API key
1. Open <https://console.groq.com/> and sign up (no card needed).
2. **API Keys → Create API Key** → name it `edxso-outreach`.
3. Copy the key immediately (it's only shown once) and paste it as `GROQ_API_KEY`.

## 5. Gmail sender + App Password
A **separate Gmail account** for sending is recommended, so your personal account isn't affected if anything misbehaves.
1. Sign in to the sender account → <https://myaccount.google.com/security> → turn on **2-Step Verification** (App Passwords require it).
2. Open <https://myaccount.google.com/apppasswords> → name it `edxso-outreach` → **Create**.
3. Copy the 16-character password and paste it as `SMTP_APP_PASSWORD`, without spaces.
4. Set `SMTP_USER` to the sender address and `SENDER_NAME` to the name shown in emails.

Notes:
- Leave `SEND_MODE=DRY_RUN` until you deliberately test `REDIRECT`.
- Consumer Gmail limits sending volume per day. The demo sends only a few dozen emails, so this isn't a concern.

## 6. Test inbox
Use an inbox you own that is **different from the sender**, e.g. your personal email, and set it as `TEST_INBOX`. In `REDIRECT` mode every "real" send goes here, with the intended creator's address shown in the subject.

## 7. Backend API secret
```bash
openssl rand -hex 32
```
Paste the result as `API_KEY`. n8n sends it in the `X-API-Key` header when calling the backend.

## 8. n8n (local, free, no Docker)
Node 22 is already installed, so n8n runs directly:
```bash
npx n8n            # first run downloads n8n; UI at http://localhost:5678
```
On first launch, create the **local owner account**. It is stored only on your machine and has nothing to do with n8n Cloud. Importing the workflows and adding credentials is covered in the README.

## 9. GitHub repository
Create an empty repository on GitHub. Don't add a README, license or `.gitignore`, since the project already has them. Then:
```bash
git remote add origin git@github.com:<you>/<repo>.git
git push -u origin main
```
