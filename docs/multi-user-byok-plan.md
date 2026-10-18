# Plan: User Accounts and Bring-Your-Own-Key

Status: Phases 0–4 done · Date: 2026-10-18

This plan moves the app from a single shared access secret with server-owned provider keys to:

- **User accounts:** sign-up, sign-in, email verification and password reset.
- **Reports owned by users:** each report belongs to one account, and nobody else can see or reach it.
- **Bring your own key (BYOK):** each user stores their own provider API keys, encrypted. Every LLM call for a report uses its owner's key.
- **More providers:** Anthropic, OpenAI and OpenRouter join Gemini and DeepSeek.

The work is split into eight phases. Each phase ships on its own, keeps the test suite green, and leaves the app usable.

---

## 1. Decisions

| Topic | Decision |
|---|---|
| Hosting | One Mac mini, Docker Compose, served at `https://repgen.gengardev.com.br` through a Cloudflare Tunnel (Section 3.1) |
| Database | PostgreSQL (new compose service) |
| Sign-up | **Open to anyone.** Users bring their own keys, so the server never pays for LLM calls. Email verification and Cloudflare Turnstile guard sign-up, and quotas protect the Mac's CPU and disk. `PDF_REPORT_SIGNUP_MODE=open` (with `closed` kept as a kill switch). |
| Sign-in | Email + password, built in-house. OAuth can be added later on the same `users` table. |
| Email | A transactional provider over SMTP (Resend, Postmark, Amazon SES and others all offer SMTP), with SPF, DKIM and DMARC records added in Cloudflare DNS for `gengardev.com.br` |
| Key encryption | AES-256-GCM envelope encryption, with the master key from a Docker secret |
| TLS | Cloudflare terminates HTTPS; no reverse proxy in compose |
| Existing reports | Imported into a **demo account** (Section 9) |
| Providers in the first release | Gemini, DeepSeek, Anthropic, OpenAI, OpenRouter. Mistral and Groq later. User-supplied base URLs stay out of scope (see 6.6). |
| Keys per provider | One per user per provider. Several labelled keys can come later without a schema change. |
| Default quotas per user | 10 GB storage, 1 job running at a time, 50 reports per day, all configurable |

---

## 2. Today's state and what blocks multi-user

| Area | Today | Problem for multi-user |
|---|---|---|
| Auth | One shared secret (`app/security.py`), stateless HMAC tokens, revocation list in `workspace/auth/` | No identities, no ownership |
| Provider keys | `GEMINI_API_KEY` / `DEEPSEEK_API_KEY` read from env in `app/services/llm/client.py` (`get_gemini_client`, `get_deepseek_client`) | The server pays for every call. Keys have to come from the report's owner instead. |
| Sessions (reports) | JSON files `workspace/sessions/{job_id}.json` with `fcntl` locks (`session_manager.py`), no owner field | No ownership, no queries ("my reports"), file locks are single-host only |
| File storage | Flat folders: `workspace/uploads`, `filtered`, `scripts`, `reports` | Files from all users are mixed together |
| **Sandbox mounts** | `executor_service._sandbox_bind_args` mounts *all* of `uploads/` (read-only) and *all* of `filtered/`, `scripts/`, `reports/` (writable) | **Blocker:** one user's generated script could read, or overwrite, another user's data. Must become per-report mounts before any second user exists. |
| Recent sessions | `list_recent_sessions()` returns every session | Shows other users' reports |
| Layout templates | One shared file, `workspace/layout_templates.json` | Users would edit each other's templates |
| Admin | `/api/admin/*` behind `APP_ADMIN_SECRET` | Should be a user role |
| LLM usage | Character counts per session (`record_llm_usage`) | Users need token counts and cost for their own keys |
| Concurrency | Global cap (`job_runner.py`) | One user can take every slot |
| Login rate limit | In-memory, per IP | Also needs per-account limits, and signup/reset limits |

Parts of the current code that make this easier:

- **Every LLM call already runs inside `llm_operation_context(job_id=...)`.** That context is the natural place to resolve "whose key?" without threading a user ID through about 40 functions.
- **All model calls go through one function,** `client.call_llm` (and `call_llm_with_pdf`). There is one spot to swap the credential source and one patch target for tests.
- **Reports are only read and written through the `session_manager` functions** (`get_session`, `update_session`, `add_log`, and so on). Those functions can keep their signatures while their storage moves to Postgres.
- **PDF context files are already read via text extraction** (`data_analyzer` with pypdf) when they contain text. Only scanned PDFs need a model that reads PDFs natively.

---

## 3. Target architecture

```text
Browser ──HTTPS──► Cloudflare edge (TLS, WAF, Turnstile)
                     │  Cloudflare Tunnel (outbound connection from the Mac)
                     ▼
Mac mini ─ Docker (Linux VM)
  cloudflared ──HTTP──► FastAPI app (uvicorn, 1 process)
                              │  ├─ auth: server-side sessions (Postgres)
                              │  ├─ routers: owner-scoped
                              │  ├─ job_runner: global and per-user slots
                              │  ├─ llm/: provider adapters + credential resolver
                              │  └─ executor: bubblewrap, mounts per report only
                              ├──► PostgreSQL (users, sessions, reports, credentials, usage)
                              ├──► workspace/tenants/{user_id}/reports/{report_id}/…
                              └──► SMTP provider (verification, reset)
```

- **One app process.** In-process job threads, the in-memory rate limiter, and per-report serialization all assume a single process. Running several replicas later needs a Postgres-backed job queue (Section 11).
- **Sync SQLAlchemy 2.x with psycopg 3.** The pipeline already runs synchronously in worker threads, so a sync engine with a connection pool fits without rewriting it as async.
- **Alembic** for schema migrations.
- **Report state stays a JSON document,** stored in a `JSONB` column. Frequently queried fields (owner, status, step, title, timestamps) become real columns. This keeps the pipeline code unchanged and makes listing and ownership checks fast.

### 3.1 Deployment: Mac mini and Cloudflare Tunnel

The host shapes several parts of the design.

**The app must run inside Docker on the Mac.**
- bubblewrap is Linux-only. Docker Desktop (or OrbStack/Colima) runs containers in a Linux VM, where bubblewrap works with the compose settings already in place (`seccomp=unconfined`, `apparmor=unconfined`).
- Run directly on macOS, the sandbox is unavailable and report generation is refused (or, with `PDF_REPORT_ALLOW_UNSAFE_SANDBOX=1`, runs with weak isolation). With open sign-up, that is not acceptable.
- Phase 0 verifies bubblewrap inside the container on the Mac mini before anything else.

**Keep `cloudflared` on the Mac host, as it runs today.**
- Its ingress points at `http://localhost:8000`.
- **Publish the app's port on `127.0.0.1` only.** The LAN can't reach the app directly, so every outside request arrives through the tunnel, which makes Cloudflare's headers trustworthy.

**Client IP.** Behind the tunnel, every request comes from `cloudflared`. Take the real address from `CF-Connecting-IP`, but only when the request came through Docker's gateway, which is where the host `cloudflared` connects from: `PDF_REPORT_CLIENT_IP_HEADER=CF-Connecting-IP`, `PDF_REPORT_TRUSTED_PROXIES=172.16.0.0/12,192.168.65.0/24`. Rate limits and audit events use this address.

**TLS and cookies.** Cloudflare serves HTTPS, so `Secure` cookies work, while the tunnel carries plain HTTP inside Docker. Set `PUBLIC_BASE_URL=https://repgen.gengardev.com.br` for email links and redirects. Turn on HSTS and "Always Use HTTPS" in the Cloudflare dashboard.

**Request timeout.** Cloudflare drops a proxied request after about 100 seconds (HTTP 524). Most work already runs in the background with polling, but two endpoints wait for LLM calls inside the request:
- `PUT /api/sessions/{id}/settings` when the language changes (`translate_workspace`);
- `POST /api/sessions/{id}/explore`.

Phase 0 turns both into background jobs that the UI polls. Component previews (a 30-second sandbox cap) stay synchronous.

**Upload size.** Cloudflare's free plan accepts request bodies up to 100 MB. The 50 MB per-file limit fits, but a multi-file upload is one request, so cap the total per request at 90 MB.

**Caching.** Add a Cloudflare cache rule that bypasses the cache for `/api/*` and `/session/*`, and send `Cache-Control: private, no-store` on every authenticated response. That stops reports and PDFs from being cached at the edge. Static assets under `/static/` can stay cached.

**Cloudflare features worth using (free plan):**
- **Turnstile** on sign-up, login and forgot-password (Phase 2). The CSP has to allow `https://challenges.cloudflare.com` in `script-src` and `frame-src`.
- A **WAF rate-limiting rule** on `/api/auth/*` as an outer layer in front of the app's own limits.
- **Cloudflare Access** (Zero Trust) on `/admin*` and `/api/admin/*`, so admin pages need a second, Cloudflare-side login as well as the admin role. The rest of the site stays public.

**Running a server on one Mac:**
- Disable sleep, and enable "start up automatically after a power failure".
- Start Docker at login.
- `restart: unless-stopped` on every service.
- Ideally a UPS.

**Storage.** Keep Postgres data in a **named Docker volume** (inside the VM), not a bind mount to macOS; Postgres on macOS bind mounts is slow and fragile. The `workspace/` tenant files can stay on a bind mount, so they're easy to back up, or move to a named volume for speed.

**Memory.** Every sandboxed script may use up to 2 GB. Size the Docker VM's memory to `global job slots × 2 GB` plus Postgres and the app, and set `PDF_REPORT_MAX_CONCURRENT_JOBS` to match. For example, a 16 GB Mac with 10 GB given to Docker supports about 3 concurrent jobs.

**Backups leave the machine.** Everything lives on one disk, so send the nightly `pg_dump` and the `workspace/` backup off the Mac: Cloudflare R2, an external drive, or another machine. Keep the encryption master key in a separate place (a password manager), never next to the DB dump.

### New dependencies

`sqlalchemy>=2`, `alembic`, `psycopg[binary]>=3`, `argon2-cffi`, `email-validator`, `anthropic` (1.x). `cryptography` (AES-GCM) and `openai` are already installed. Pin all of them in `requirements-lock.txt`.

Note: `anthropic` 1.x is built on `httpx2`, not `httpx`. The two install side by side. Catch Anthropic's own exception classes, not `httpx` ones.

---

## 4. Data model

All IDs are UUIDs. Timestamps are `timestamptz`.

```text
users
  id, email (citext, unique), password_hash, display_name,
  role ('user' | 'admin'), status ('active' | 'disabled'), is_demo bool,
  email_verified_at, preferences jsonb (default provider/model, locale),
  created_at, updated_at, last_login_at

auth_sessions                      -- replaces HMAC tokens and the revocation file
  id, user_id → users, token_hash (sha256, unique),
  created_at, last_seen_at, idle_expires_at, absolute_expires_at,
  revoked_at, ip, user_agent

email_tokens                       -- verification, reset, email change
  id, user_id, purpose ('verify_email' | 'reset_password' | 'change_email'),
  token_hash (unique), new_email, expires_at, used_at, created_at

provider_credentials
  id, user_id → users, provider, label,
  ciphertext bytea, nonce bytea, wrapped_dek bytea, dek_nonce bytea, kek_version int,
  key_last4, status ('valid' | 'invalid' | 'unchecked'), last_error,
  available_models jsonb, last_validated_at, created_at, updated_at
  unique (user_id, provider)       -- D5

reports                            -- replaces workspace/sessions/*.json
  id (text, the existing job_id format), owner_id → users,
  title, status, current_step, pdf_version, state jsonb,
  storage_bytes bigint, created_at, updated_at, deleted_at
  index (owner_id, updated_at desc)

layout_templates                   -- replaces workspace/layout_templates.json
  id, owner_id → users (null = built-in), data jsonb, created_at, updated_at

llm_usage_events                   -- replaces char counts inside session JSON
  id, user_id, report_id, provider, model, operation,
  input_tokens, output_tokens, duration_ms, status, error_code, created_at
  index (user_id, created_at)

audit_events
  id, user_id (nullable), event, ip, details jsonb, created_at
  -- login ok/failed, signup, password change/reset, key added/replaced/removed,
  -- admin actions, account deletion
```

Plaintext keys never appear in any column, log line, API response, error message, or report state.

---

## 5. Authentication design

### Passwords

- Hash with **argon2id** (`argon2-cffi`). Check `needs_rehash` on login so parameters can be raised later.
- Policy: at least 12 characters, a maximum length (for example 256) so huge inputs can't be used for denial of service, and no composition rules. An optional breached-password check (HIBP k-anonymity range API) can come later because it needs outbound HTTP.
- Changing the password requires the current one, and revokes every other session.

### Sessions

- On login: create a random 32-byte token, store only its SHA-256 hash in `auth_sessions`, and send it in the `pdf_report_session` cookie (`HttpOnly; Secure; SameSite=Lax; Path=/`).
- Expiry: idle 7 days (sliding; `last_seen_at` updates at most once every few minutes) and absolute 30 days.
- Logout revokes that row. Add "log out everywhere" and a list of active devices.
- A new token on every login prevents session fixation.
- `X-Access-Token` for API clients stays possible later as personal access tokens. It's out of scope for the first release.

### CSRF

The app authenticates with a cookie and accepts form posts, so add CSRF protection on top of `SameSite=Lax`, using the double-submit pattern:

- Issue a non-HttpOnly `csrf_token` cookie.
- Require a matching `X-CSRF-Token` header on every request that changes state (anything except GET, HEAD and OPTIONS) and on all `/api/auth/*` POSTs.
- `api.js` attaches the header automatically.

### Email flows

- **Sign-up:** open to anyone (D: open). Create the user unverified and email a verification link (token valid 24 h, single use, stored as a hash).
  - Unverified users can sign in but cannot add keys or create reports.
  - Unverified accounts older than 7 days are deleted by the cleanup task.
- **Password reset:** "forgot password" always answers the same way whether or not the email exists, so accounts can't be enumerated. The token is valid 30 minutes, single use, and using it revokes all sessions.
- **Email change:** send a link to the new address and a notice to the old one.
- Emails go out from a background task, so slow SMTP never blocks a request. Templates live in `app/templates/email/` (Portuguese first, English second).

### Rate limits

Replace the current `LoginRateLimiter` with a small limiter module that keeps several buckets:

| Action | Limit |
|---|---|
| Login | Per IP, and per account email (stops slow guessing from many IPs) |
| Sign-up | Per IP |
| Forgot password / resend verification | Per IP, and per email |

- In-memory is enough for one process. Section 11 covers moving it into Postgres.
- Key the limits on the real client IP from `CF-Connecting-IP`, trusted only for requests through Docker's gateway (3.1).
- **Turnstile:** sign-up, login (after the first failure) and forgot-password require a Cloudflare Turnstile token. The server verifies it with Cloudflare's `siteverify` endpoint before doing any other work. This stops scripted sign-ups and credential stuffing cheaply.
- After repeated failures on one account, add a growing delay instead of locking the account. Locking would let anyone lock out a known user.

### Roles and admin

- `role = 'admin'` replaces `APP_ADMIN_SECRET`, which is removed.
- The first admin is created with a CLI command: `python -m app.cli create-admin --email …`.
- Admins can list users, disable accounts, see usage totals, and run cleanup.
- Admins **cannot** read anyone's keys or report contents through the API.

---

## 6. Bring your own key

### 6.1 Encryption at rest

The scheme is envelope encryption:

- **Master key (KEK):** `APP_ENCRYPTION_KEYS="v2:<base64 32 bytes>,v1:<base64 32 bytes>"`, with `APP_ENCRYPTION_ACTIVE_KEY=v2`. Load it from a Docker secret in production.
- **Per credential:** generate a random 32-byte data key (DEK).
  - Encrypt the API key with AES-256-GCM under the DEK.
  - Wrap the DEK with AES-256-GCM under the active KEK.
  - Store the ciphertext, both nonces, the wrapped DEK and `kek_version`.
- **AAD** (authenticated additional data): `f"{user_id}|{provider}|{credential_id}"` on both layers. A ciphertext copied into another user's row fails to decrypt.
- **KEK rotation:** add a new version, make it active, then run `python -m app.cli rewrap-keys`. That re-wraps only the DEKs; the API key ciphertexts don't change. Remove the old version once nothing references it.
- **If the KEK is lost,** every stored key becomes unreadable and users have to enter them again. Keep the KEK out of DB backups and back it up separately.
- Code goes in `app/services/crypto.py` (`encrypt_secret`, `decrypt_secret`, `rewrap`), with unit tests for:
  - a round trip;
  - a wrong AAD failing;
  - rotation;
  - any tampered field (ciphertext, nonce or wrapped DEK) failing.

### 6.2 Key lifecycle (API and UI)

- **Add or replace:** `PUT /api/credentials/{provider}` with `{api_key}`.
  1. Validate the key with the provider before saving (6.4).
  2. Encrypt and store it with `status=valid`, `key_last4`, and `available_models`.
  3. Write an audit event.
  4. Don't return the key.
- **Test:** `POST /api/credentials/{provider}/test` re-validates the key and refreshes the model list.
- **Delete:** `DELETE /api/credentials/{provider}` removes the row; the key is gone immediately.
- **List:** `GET /api/credentials` returns provider, label, `key_last4`, status, `last_validated_at` and the model count. It never returns the key.
- **Automatic invalidation:** when a provider answers 401/403 during a job, mark the credential `invalid` with `last_error`, so the UI can prompt the user to update it.

### 6.3 Using the owner's key on every call

1. Add `user_id` to `llm_operation_context`. The top-level contexts set it: pipeline, script generation, refinement, translation and the explorer. Inner contexts inherit it automatically through `**current`, as `job_id` already does.
   - Fallback when `user_id` is missing: resolve `job_id → reports.owner_id` once and cache it in the context.
   - A call with neither raises `MissingCredentialError`. Nothing ever falls back to a server key.
2. Add a new module, `app/services/llm/credentials.py`, with `resolve_api_key(user_id, provider) -> str`.
   - It reads and decrypts the key once per operation, caching it in the context dict only, never globally.
   - It raises `MissingCredentialError(provider)` when the user has no key, and `InvalidCredentialError(provider)` when the key is marked invalid.
3. `client.py` builds provider clients from the resolved key through the adapter (6.5), replacing `get_gemini_client()` and `get_deepseek_client()`.
4. **Errors:** both new errors implement `as_error_dict()` with `retryable: False`, using codes `missing_provider_key` and `invalid_provider_key`.
   - The pipeline's existing handlers then show a clear message, such as "Adicione sua chave Anthropic em Configurações → Chaves".
   - The frontend turns that code into a link to the keys page.
   - Auth errors are never retried. Today's retry loop would otherwise retry them three times.
5. **Fallback** (`PDF_REPORT_LLM_FALLBACK_*`): becomes a user preference, and may only pick from the user's *own* valid keys. The server-wide environment fallback is removed.
6. **Dev and test only:** `PDF_REPORT_DEV_SERVER_KEYS=1` lets the resolver fall back to `GEMINI_API_KEY` / `DEEPSEEK_API_KEY` from the environment. The app refuses to start with that flag when `APP_ENV` is production, so the operator's key is never billed for user traffic.
7. **Scanned PDFs** (no extractable text): these need a model that reads PDFs natively.
   - Use the selected provider if its adapter reports `supports_pdf`.
   - Otherwise use the user's other PDF-capable key (Gemini or Anthropic) when one exists.
   - Otherwise skip insights for that PDF with a visible note ("PDF digitalizado: adicione uma chave Gemini ou Anthropic para extrair conteúdo").
   - Text PDFs are unaffected and work with any provider.
8. **Usage:** each adapter returns token counts. `call_llm` writes `llm_usage_events` rows in place of `record_llm_usage` character counts, and the per-report summary is computed from those rows.

### 6.4 Validating a key without spending tokens

Each adapter implements `validate_key(api_key) -> list[ModelInfo]`, using the provider's free model-listing call:

| Provider | Call |
|---|---|
| Anthropic | `client.models.list()` |
| OpenAI / OpenRouter / DeepSeek | `GET /models` through the OpenAI client |
| Gemini | `client.models.list()` |

A 401/403 means the key is invalid; network errors and 5xx mean "couldn't validate, try again". The returned model list fills `available_models`, so the model picker shows only models the key can actually use. Time out after about 10 seconds.

### 6.5 Provider adapters

Replace the `if provider == "gemini" … else` branch in `_request_llm_content` with a registry in `app/services/llm/providers/`:

```python
class ProviderAdapter(Protocol):
    id: str                      # "anthropic"
    label: str                   # "Anthropic"
    default_model: str
    curated_models: list[ModelInfo]   # shown before the key is validated

    def build_client(self, api_key: str, timeout_s: int): ...
    def generate(self, client, *, model, system, user, timeout_s,
                 json_output: bool, pdf_bytes: bytes | None) -> GenerationResult: ...
    def validate_key(self, api_key: str) -> list[ModelInfo]: ...
    def supports_pdf(self, model: str) -> bool: ...
    def classify_error(self, exc) -> Literal["auth", "rate_limit", "timeout", "retryable", "fatal"]: ...

@dataclass
class GenerationResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    stop_reason: str | None
```

Rules for every adapter:

- Disable the SDK's own retries (`max_retries=0`). `call_llm` already owns retries, budgets and timeouts.
- Map "output truncated" (`max_tokens` / `length`) to `LLMContractError`, as DeepSeek does today.

| Adapter | Notes |
|---|---|
| `gemini.py` | Move today's Gemini code here unchanged: JSON mime type, PDF parts, the gemma exceptions. |
| `openai_compatible.py` | One class configured by `base_url`, covering **DeepSeek** (`https://api.deepseek.com`), **OpenAI**, and **OpenRouter** (`https://openrouter.ai/api/v1`). JSON via `response_format={"type": "json_object"}` where the provider supports it; the strict parsers in `parsing.py` stay the safety net. |
| `anthropic.py` | Details below. |
| Mistral, Groq (later) | Probably fit the OpenAI-compatible adapter; confirm their JSON mode and error codes at implementation time. |

**Anthropic adapter specifics:**

- **Client:** `anthropic.Anthropic(api_key=key, max_retries=0, timeout=timeout_s)`, built per user key and closed after the operation.
- **Request:** `client.messages.create(model=…, max_tokens=16000, system=system, messages=[{"role": "user", "content": user}])`. `max_tokens` is required. Use about 16000 for non-streaming calls; switch to streaming with `.get_final_message()` if an operation ever needs much larger outputs.
- **Models:**
  - Catalog default: `claude-opus-5-5`, with `claude-sonnet-5-5` and `claude-haiku-5-5` as cheaper options.
  - Use the exact IDs, with no date suffixes.
  - Thinking is always on for Opus 5.5. Don't send `thinking: {type: "disabled"}` or `budget_tokens` (both return 400); control depth with `output_config={"effort": …}` instead. Suggested effort: `low` for routing and titles, `medium` (Opus 5.5's default) for planning and code.
- **JSON output:** structured outputs (`output_config={"format": …}` with a JSON schema) for the contract operations, where a schema exists. Otherwise, prompt-only JSON plus the existing parsers. Assistant-message prefill is not supported (400), so don't use it to force JSON.
- **PDF input:** a `{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": …}}` block before the text block. Limits are 32 MB per request and 600 pages, so check size before sending.
- **Refusals:**
  - Check `stop_reason == "refusal"` before reading content, and map it to a non-retryable `LLMContractError` with code `model_refusal`.
  - Opt into server-side fallback on Opus 5.5 by default: `client.beta.messages.create(..., betas=["server-side-fallback-2026-07-01"], fallbacks="default")`. It re-runs a declined request on a fallback model within the same call, billed to the user's key.
  - Show a "served by" note when a fallback model answered.
  - Make it a user preference that defaults to on.
- **Errors:** map `AuthenticationError` and `PermissionDeniedError` to `auth`, `RateLimitError` to `rate_limit` (honor `retry-after`), `APITimeoutError` to `timeout`, `APIStatusError` with status 5xx and `APIConnectionError` to `retryable`, and other 4xx to `fatal`.

### 6.6 Out of scope: user-supplied base URLs

Letting users point the app at a self-hosted OpenAI-compatible endpoint (Ollama, vLLM) means the *server* makes HTTP requests to user-chosen addresses. That is a server-side request forgery (SSRF) risk: requests could reach Postgres, cloud metadata endpoints, or the LAN. Defer it. If it's added later, it needs:

- HTTPS only;
- the hostname resolved once and checked against private, link-local and loopback ranges;
- a pinned IP for the connection;
- no redirects;
- an admin allowlist.

### 6.7 Model catalog per user

`GET /api/models` becomes per user. For each provider it returns the user's credential status and the models available to that key (falling back to curated defaults while the key is unchecked). Providers without a key come back disabled with a "add key" hint. `model_config.py` keeps the curated defaults, labels and the `MODEL_REPLACEMENTS` migration map, grouped by adapter.

---

## 7. Isolating users' data

### 7.1 Ownership checks

- One dependency, `current_user = Depends(require_user)`, on every non-public route.
- `require_report(report_id, user)` replaces `require_session`. It returns **404** when the report belongs to someone else, never 403, so users can't discover which IDs exist.
- Every listing query filters by `owner_id`: recent reports, layout templates, usage.
- Built-in templates (`owner_id IS NULL`) are readable by everyone and writable by nobody. User templates are private.
- **Route-coverage test:** enumerate `app.routes`. Every `/api/*` route must either be in an explicit public allowlist (`/api/auth/login`, `/api/auth/signup`, …) or reject a second user's request for the first user's report. A new route without an ownership check then fails CI.

### 7.2 Per-report storage

New layout:

```text
workspace/tenants/{user_id}/reports/{report_id}/
  uploads/      original files            (sandbox: read-only)
  filtered/     preprocessed tables       (sandbox: writable)
  scripts/      generated scripts         (sandbox: writable)
  output/       report.pdf, previews, manifests, explorer charts (sandbox: writable)
```

- Add a `ReportPaths` dataclass (`app/utils/report_paths.py`) built from `(user_id, report_id)`. It replaces the global `UPLOAD_DIR`, `FILTERED_DIR`, `SCRIPT_DIR` and `REPORT_DIR` constants used in `pipeline_service`, `web_helpers`, `exploration_service`, `exploration_charts` and `cleanup_service`.
- **Sandbox:** `execute_script(script, paths: ReportPaths, …)`. `_sandbox_bind_args` then mounts only that report's four folders, plus the interpreter, system paths and a private tmp. The Python guard's read and write directory lists use the same folders.
  - Metric scripts that run in temporary directories still get read-only `uploads/` and `filtered/` for their report.
  - The `sandbox_support` helpers stay mounted read-only and are shared; they contain no user data.
- **Cleanup:** deleting a report removes its folder (`rm -rf` of one directory instead of today's file-name matching). Deleting an account removes `tenants/{user_id}`.
- **Quotas:**
  - Track `storage_bytes` per report; uploads check against the user's limit before they're written (Section 1 defaults).
  - `job_runner` gets per-user slots on top of the global cap. A user's second job waits ("Aguardando seu relatório anterior terminar...").
  - A daily report-creation limit per user.

### 7.3 Account deletion and data export

- `DELETE /api/account` (requires the password) revokes sessions, deletes credentials, reports, files and templates, and keeps an anonymized audit record.
- Optionally later: "export my reports" as a zip of PDFs and the report JSON.

---

## 8. Frontend

All code goes in `static/js/` (the CSP blocks inline scripts). UI text is in Portuguese.

| Page / change | Notes |
|---|---|
| `/login`, `/signup`, `/verify-email`, `/forgot-password`, `/reset-password` | New `static/auth.html` and `static/js/auth.js`. Replaces the access-secret modal in `app.js`. |
| Account settings | Profile, password change, active devices with "log out everywhere", delete account |
| **Keys page** | One card per provider: add/replace (password-type input, never shown again), "Testar", delete, last four characters, status badge, last validated time, model count |
| Usage page | Tokens per provider and model per day, plus the most expensive reports. Optionally an estimated cost from a price table per adapter. |
| Workspace | Recent sessions shows only your reports. The provider/model picker shows only providers with a valid key. A `missing_provider_key` / `invalid_provider_key` error links to the keys page. |
| `api.js` | Adds the `X-CSRF-Token` header. A 401 redirects to `/login?next=…`. |
| Admin page | Users table (status, role, usage), workspace usage, cleanup. Also behind Cloudflare Access. |

---

## 9. Migrating existing data

Existing reports go into a **demo account**:

```text
python -m app.cli create-demo-user --email demo@gengardev.com.br
python -m app.cli import-legacy --owner demo@gengardev.com.br [--dry-run]
```

**The demo account** is a normal, verified user created by the CLI.
- It has a random password that the CLI prints once, so only you can sign in to it.
- It has no provider keys. Its imported reports can be viewed, edited by hand and downloaded, but regenerating with an LLM needs a key added to that account.
- `users.is_demo = true` marks it, which allows two optional extras later:
  - showing its reports as public examples on the sign-up page;
  - making it read-only, so its login could be shared without anyone deleting reports or storing keys in it.
- Neither extra is in the first release.

**The import:**

1. Read each `workspace/sessions/*.json` through `normalize_session`.
2. Create a `reports` row owned by the demo account, using the same `job_id` so existing links keep working.
3. Move the files referenced in `files[].path`, `preprocessed_path`, `filtered_path`, `script_path`, `pdf_path`, the `.manifest.json` files and the `explore_*` folders into the new per-report layout. Rewrite those paths in `state`.
4. Import `workspace/layout_templates.json` user templates as the demo account's templates. Built-ins come from code.
5. Report counts and files that couldn't be matched. A `--dry-run` changes nothing.
6. Keep the old folders until the operator deletes them by hand.

Test it with a fixture workspace (Phase 1 acceptance).

---

## 10. Implementation phases

Size estimates: S is about 1–2 days, M is about 3–5 days, L is about 1–2 weeks of focused work.

### Phase 0 — Groundwork on the current single-user app (M)

**Status (2026-10-18): done and deployed on the Mac mini.**
- bubblewrap works in Docker on the Mac mini with the compose settings.
- New reports use `workspace/tenants/default/reports/{job_id}/`; `default` becomes the owner's user ID in Phase 3.
- Existing reports keep the shared folders until the Phase 1 import copies them.

Goal: get the risky refactors in while there's still one user.

1. **Check the sandbox on the Mac mini first.** Run the app's container on the Mac and execute `docker compose exec app env APP_ENV=test PDF_REPORT_REQUIRE_BWRAP=1 python -m unittest tests.test_sandbox_executor`.
   - If bubblewrap fails inside Docker Desktop's VM, fix that (VM settings, or OrbStack) before continuing. Open sign-up depends on it.
   - Publish the app port on `127.0.0.1` only (3.1).
2. **Background translation and exploration** (3.1, request timeout): both endpoints queue a job through `run_job` and return right away; the UI polls the session as it already does for reports. Explorer turns get a `pending` state in `explore_history`.
3. **Provider adapter registry** (6.5). Move the Gemini and DeepSeek code into adapters; `_request_llm_content` dispatches through the registry. Adapters return `GenerationResult` with token counts.
4. **`ReportPaths` and per-report sandbox mounts** (7.2), still using env keys and JSON sessions. New reports use the per-report layout; old ones are read through a compatibility path.
5. **Usage capture:** store input and output token counts in the existing usage summary.
6. **Cache headers:** `Cache-Control: private, no-store` on authenticated responses, plus the Cloudflare cache-bypass rule for `/api/*` and `/session/*`.
7. Tests:
   - Adapter unit tests with fake clients.
   - A sandbox test proving report A's script can't see report B's uploads or outputs. Use the same guard-bypass technique as `BubblewrapIsolationTests`.
   - The existing 230 tests still pass.

**Done when:** bubblewrap is confirmed working on the Mac mini, translation and exploration no longer hold a request open, behavior is otherwise unchanged, and the sandbox only sees the current report.

### Phase 1 — Postgres foundation (L)

**Status (2026-10-18): done.** Differences from the steps below:
- **No `migrate` service.** The app applies migrations itself the first time it connects (`app/db.py`, under a Postgres advisory lock), and so does `python -m app.cli migrate`. One fewer container; `PDF_REPORT_AUTO_MIGRATE=0` turns it off.
- **Backups are a host script,** `scripts/backup.sh` (run from cron or launchd), not a sidecar. `BACKUP_SYNC_COMMAND` copies the files off the Mac. A restore was rehearsed with the production compose file.
- **The `users` table exists already,** so the import can create the demo account now. Its password is set in Phase 2. Emails are stored lowercased with a unique index instead of `citext`.
- **Tests:** instead of a rollback per test, the `repgen_test` database is emptied once per run (its name must end in `_test`). Background jobs and the test client use their own connections, which per-test rollback can't cover. Local Postgres comes from `compose.dev.yml` (dev and test databases), and CI runs a `postgres` service.
- `import-legacy` **copies** files rather than moving them, so the old folders stay until deleted by hand (step 6 of Section 9). Reports created after Phase 0 (already in per-report folders) are copied into the owner's folder too.
- The explorer's file lock in the shared scripts folder is gone; the in-process lock and the pending turn stored in the database cover it.
- Postgres can't store NUL characters or NaN/Infinity in JSONB. `app/db.py` drops NULs and stores NaN/Infinity as `null`.
- Reports created from now until Phase 3 have no owner and stay in `tenants/default/`. Phase 2 assigns them (to the demo account or the admin).

1. Add a `postgres` service to `docker-compose.yml`: data in a named volume (not a macOS bind mount), no published port, a healthcheck, the app waiting for it (`depends_on: condition: service_healthy`), and `DATABASE_URL`. Run `alembic upgrade head` as a one-shot `migrate` service before the app starts.
2. Add `app/db.py` (engine, session factory, `transaction()` context manager), `app/models/db.py` (SQLAlchemy models from Section 4), and `alembic/`.
3. **Reimplement `session_manager` on the `reports` table, keeping every public signature.**
   - `update_session` becomes `SELECT … FOR UPDATE`, then merge, `normalize_session`, and write, all in one transaction.
   - `add_log`, `append_stage_event` and `reserve_session_run` work the same way.
   - Remove the `fcntl` lock files. Pipeline code doesn't change.
4. Move layout templates to `layout_templates` (with built-ins seeded from code), and the revoked-token list to the DB (dropped again in Phase 2).
5. Write `app/cli.py` with `import-legacy` (Section 9).
6. **Tests:** run against a real Postgres. Add a `postgres` service container to CI, and `docker compose -f compose.test.yml` locally.
   - A base `DbTestCase` creates the schema once per run and wraps each test in a transaction that's rolled back.
   - Don't substitute SQLite: JSONB and row-locking behave differently.
7. **Backups:** a nightly `pg_dump` sidecar with retention, plus a backup of `workspace/`, both copied **off the Mac** (Cloudflare R2, an external drive, or another machine). Document and rehearse a restore.

**Done when:** the app runs on Postgres with the same features and all tests pass, and an imported legacy workspace renders its old reports.

### Phase 2 — Accounts and sign-in (L)

**Status (2026-10-18): done.** Notes and differences:
- **Sign-up ships closed** (`PDF_REPORT_SIGNUP_MODE=closed`). Until Phase 3, every account sees every report, and until Phase 4 they use the server's provider keys, so open it only after both. Accounts come from `python -m app.cli create-admin` / `create-user` meanwhile.
- **Email links carry the token in the URL fragment** (`/verify-email#token=...`) and the page POSTs it. Mail scanners that prefetch links can't use them up, and tokens never reach server or Cloudflare logs.
- **Sign-up doesn't sign you in.** Doing so would reveal whether the email was new. The page says to check the inbox, then sign in (unverified accounts can sign in but not create reports).
- **Repeated failed logins on one account add a growing delay** (2 s doubling to 15 min after the fourth failure), not a lock. Turnstile is required from the first failure on.
- A new email link cancels older unused links of the same kind.
- `/api/admin/*` needs `role = 'admin'`; there is no admin page yet (Phase 6).
- **New reports already get `owner_id` and live in `tenants/{user_id}/`** (part of Phase 3 step 2). The access checks came in Phase 3.
- `X-Access-Token` is gone; API clients will get personal access tokens later (Section 11).
- The demo account gets a password from `create-demo-user` (when it has none) or `set-password`.
- Email texts are plain text in Portuguese with a short English summary (`app/templates/email/`).

1. Tables `users`, `auth_sessions`, `email_tokens`, `audit_events`.
2. `app/services/auth_service.py`:
   - Account and session operations: sign-up, login, logout, logout-all, verify, resend, forgot, reset, change password and change email.
   - Password hashing with argon2id.
   - Token creation and hashing.
3. `app/services/email_service.py`: SMTP settings (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `PUBLIC_BASE_URL`), send in the background, templates, and a fake mailbox for tests. Add the provider's SPF, DKIM and DMARC records to the `gengardev.com.br` zone in Cloudflare DNS, and send from an address on that domain (for example `no-reply@gengardev.com.br`).
4. Rewrite `app/security.py`:
   - `require_user` dependency;
   - server-side session lookup with sliding expiry;
   - CSRF double-submit;
   - multi-bucket rate limiter;
   - security headers, with the CSP extended for Turnstile (3.1);
   - real client IP from `CF-Connecting-IP`, trusted only from Docker's gateway (3.1).
   - Remove `APP_ACCESS_SECRET`, the HMAC tokens, `APP_ADMIN_SECRET` and the revocation file.
5. Open sign-up with Turnstile verification (`TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`), and `PDF_REPORT_SIGNUP_MODE=open|closed` as a kill switch.
6. `python -m app.cli create-admin`.
7. Routes in a new `app/routers/auth.py`; the `/api/auth/*` endpoints from Section 8.
   - The UI's sign-up form loads the Turnstile widget from `static/js/auth.js`, never from inline script.
8. Frontend auth pages (Section 8).
9. Tests:
   - every auth flow through `TestClient`, including expired, reused and tampered tokens;
   - no account enumeration;
   - rate limits;
   - CSRF rejection;
   - session revocation on password change and reset;
   - unverified users blocked from report creation;
   - Turnstile failures rejected, using a fake verifier in tests;
   - the client IP header ignored unless the request comes from the trusted proxy.

**Done when:** anyone can sign up at `https://repgen.gengardev.com.br`, verify, sign in and out, and reset passwords by email, and the shared secret is gone. All existing reports belong to the demo account.

### Phase 3 — Report ownership (M)

**Status (2026-10-18): done.** Notes:
- Migration `0003` gives reports (and non-built-in templates) that still had no owner to the demo account, else the first admin, then makes `reports.owner_id` NOT NULL. `assign-unowned` is gone.
- Route handlers use `require_session(job_id, request)`; `get_session` (no owner check) remains for background jobs. Templates go through the owner too, including the pipeline's template choice (it uses the report owner's templates).
- Built-in templates are read-only for everyone. Saving one in the editor creates a private copy, and the server always assigns new template ids so nobody can claim a built-in id.
- The per-user job slot also covers explorer questions, so a question waits while that user's report is generating.
- `tests/test_isolation.py` walks every route; a mutation check (owner check removed) makes it fail.
- Account deletion refuses the only admin (`last_admin`).

1. `reports.owner_id` is required. `require_report(report_id, user)` is on every report route in `app/routers/api.py`.
2. Per-user listing, templates, and storage under `tenants/{user_id}/`.
3. Quotas and per-user job slots (7.2).
4. Account deletion (7.3).
5. **The route-coverage isolation test** (7.1), plus isolation tests for explorer charts, file downloads, component previews and PDF download.

**Done when:** two test users can't see, change, download or run anything of each other's, and CI enforces it for new routes.

### Phase 4 — Bring your own key (L)

**Status (2026-10-18): done.** Notes:
- Gemini and DeepSeek keys are managed on `/keys`. New provider adapters remain in Phase 5.
- Production compose mounts the versioned keyring as a Docker secret (`APP_ENCRYPTION_KEYS_FILE`); local development can use `APP_ENCRYPTION_KEYS`. Production startup requires encryption configuration and rejects dev server keys.
- Credential resolution uses the authoritative report owner, caches decryption within the operation, and rechecks deletion, invalidation and replacement on each call. A 401/403 invalidates only the credential version that failed, without retries or fallback.
- `/api/models` returns each caller's models and credential status. `/api/model-preferences` stores defaults and fallback on the account; server and report fallback settings no longer choose a key.
- Scanned PDFs without an owned PDF-capable key are skipped with a visible note. Text PDFs keep working with either provider.
- Token-event storage and usage dashboards remain in Phase 6; the existing per-report token summaries continue to capture adapter token counts.

1. `app/services/crypto.py` (6.1) and the `provider_credentials` table.
2. Credential routes in `app/routers/credentials.py` and the keys page (6.2, Section 8).
3. `llm/credentials.py`, `user_id` in `llm_operation_context`, and adapters built from user keys (6.3).
4. Missing and invalid key errors, wired through `as_error_dict` to the UI.
5. Per-user `/api/models` (6.7). Fallback becomes a user preference limited to the user's own keys.
6. Remove env keys from the request path; `PDF_REPORT_DEV_SERVER_KEYS` for dev and test only. Update `list_models.py` to take a key argument.
7. Scanned-PDF handling based on the user's keys (6.3 step 7).
8. Log redaction: a `logging.Filter` that masks key-shaped strings (`sk-…`, `AIza…`, `sk-ant-…`, long base64) in log records and in stored `error` messages.
9. Tests:
   - crypto (round trip, wrong AAD, tamper, rotation);
   - credential CRUD never returning the key;
   - a scan that no API response, log line or session `state` contains a planted fake key;
   - user A's job can never use user B's key;
   - a 401 marks the key invalid and stops retries;
   - a missing key gives a clear error with no LLM call;
   - dev server keys refused in production.

**Done when:** every LLM call is billed to the report owner's key, and the server holds no provider keys in production.

### Phase 5 — New providers (M)

1. `anthropic.py` adapter (6.5), including PDF support, refusal handling and server-side fallback.
2. `openai_compatible.py` instances for OpenAI and OpenRouter. For OpenRouter, send the optional `HTTP-Referer` / `X-Title` headers from `PUBLIC_BASE_URL`.
3. Curated model lists per provider. Fill the OpenAI and OpenRouter entries from each provider's docs at implementation time; the live list from `validate_key` covers the rest.
4. Check that each prompt contract still parses on each provider. Run the canonical sessions (`tests/canonical/run_canonical_sessions.py`) once per provider with real keys, and fix prompts or parsers where a provider formats JSON differently.
5. Tests with fake SDK clients for each adapter: success, JSON mode, PDF, truncation, refusal, and every error class.

**Done when:** a user with only an Anthropic, OpenAI or OpenRouter key can produce a full report.

### Phase 6 — Usage, admin and operations (M)

1. `llm_usage_events` written by `call_llm`; a per-report summary computed from it.
2. Usage page, optionally with an estimated cost from a price table per adapter, marked as an estimate.
3. Optional per-user limits: monthly token cap and calls per report (extending the existing `llm_call_budget`).
4. Admin page: users, disable or enable, usage totals, workspace usage and cleanup, behind Cloudflare Access as well as the admin role (3.1).
5. Audit events for every security-relevant action; an admin view of them.

### Phase 7 — Hardening and launch (M)

1. **Security review checklist:**
   - every route has an ownership check;
   - no key in logs, responses or reports;
   - CSRF on all state-changing routes;
   - cookie flags;
   - rate limits;
   - account enumeration;
   - the sandbox can't reach the network or Postgres (`--unshare-all` gives no network);
   - the KEK isn't in DB backups;
   - SMTP credentials are scoped to sending only.
2. **Cloudflare configuration**, documented as a checklist:
   - tunnel ingress pointing to `http://localhost:8000`;
   - "Always Use HTTPS" and HSTS;
   - the cache bypass for `/api/*` and `/session/*`;
   - a WAF rate-limiting rule on `/api/auth/*`;
   - Turnstile keys;
   - a Cloudflare Access policy on `/admin*` and `/api/admin/*`.
3. **A load check on the Mac mini:** 5–10 users generating reports at once. Tune the global and per-user slots, the Docker VM memory (3.1) and the Postgres pool size.
4. **Mac host settings:** no sleep, auto-restart after power loss, Docker starting at login, and an alert if the tunnel goes down (Cloudflare can email when a tunnel is unhealthy).
5. A restore drill from the off-machine backups.
6. **Documentation:** README (setup, configuration table, compose services, CLI commands, backup and restore), AGENTS.md (new modules, test fixtures, ownership and credential rules), and user-facing help for getting each provider's key.
7. Remove the legacy compatibility paths for the flat workspace folders once the import is done.

---

## 11. Later (not in this plan)

- **Several app replicas:** a Postgres-backed job queue (`SELECT … FOR UPDATE SKIP LOCKED`), rate-limiter buckets in Postgres, and S3-compatible object storage for `tenants/`. The sandbox then copies inputs into a scratch folder.
- Invite-only sign-up mode, if open sign-up attracts abuse.
- Public demo gallery and read-only demo login (Section 9).
- Google/GitHub sign-in (an OAuth `user_identities` table linked to `users`).
- Two-factor authentication (TOTP) for admins first.
- Teams and shared reports (an `organizations` table and report sharing permissions).
- Personal access tokens for API clients.
- User-supplied OpenAI-compatible endpoints with SSRF protection (6.6).
- Cloud KMS for the master key, if the deployment moves to a cloud.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Cross-user data leak through a missed route or path | Ownership dependency, the route-coverage test, per-report sandbox mounts, and 404 for others' reports |
| Key leakage through logs, errors or report state | Log redaction filter, a planted-key scan test, keys decrypted only in memory per operation |
| Operator billed for user traffic | No server keys in production; startup check for the dev flag |
| Losing the master key | Back it up separately from the DB; document the impact and the re-entry flow |
| Prompts behave differently on new providers | Strict parsers already reject bad output; run canonical sessions per provider before enabling it |
| Abuse of server resources by open sign-ups | Turnstile, mandatory email verification, quotas, per-user job slots, daily limits, admin disable, `PDF_REPORT_SIGNUP_MODE=closed` as a kill switch |
| bubblewrap not working inside Docker on macOS | Verified first in Phase 0; the app refuses to run generated code rather than fall back silently |
| One Mac mini is the whole service (disk, power, home network) | Off-machine backups, a rehearsed restore, auto-restart settings, a UPS, Cloudflare tunnel health alerts |
| Requests cut off at Cloudflare's ~100 s limit | Translation and exploration move to background jobs in Phase 0 |
| Spoofed client IP defeating rate limits | App port bound to `127.0.0.1`; `CF-Connecting-IP` trusted only from Docker's gateway |
| Long migration window with mixed storage | Phase 0 makes per-report paths the default early; the import script has a dry run; old folders stay until removed by hand |
| Session/report write contention in Postgres | Row-level locks per report (same scope as today's file locks); move logs into their own table if needed |

---

## 13. Configuration after this plan

New: `DATABASE_URL`, `APP_ENCRYPTION_KEYS`, `APP_ENCRYPTION_ACTIVE_KEY`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `PUBLIC_BASE_URL` (`https://repgen.gengardev.com.br`), `PDF_REPORT_SIGNUP_MODE` (`open`), `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`, `PDF_REPORT_CLIENT_IP_HEADER` (`CF-Connecting-IP`), `PDF_REPORT_TRUSTED_PROXIES` (Docker's gateway networks), `PDF_REPORT_QUOTA_STORAGE_BYTES`, `PDF_REPORT_QUOTA_REPORTS_PER_DAY`, `PDF_REPORT_MAX_JOBS_PER_USER`, `PDF_REPORT_DEV_SERVER_KEYS` (dev and test only).

Removed: `APP_ACCESS_SECRET`, `PDF_REPORT_ACCESS_SECRET`, `PDF_REPORT_ALLOW_DEFAULT_SECRET`, `APP_ADMIN_SECRET`, `PDF_REPORT_LLM_FALLBACK_PROVIDER` / `_MODEL_NAME` (now a user preference), and `GEMINI_API_KEY` / `DEEPSEEK_API_KEY` in production.

Unchanged: the sandbox, job, upload, timeout and cookie settings from the current README.
