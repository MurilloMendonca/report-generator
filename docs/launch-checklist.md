# Production launch and operations

Run the host checks on the Mac mini. Local tests do not establish that DNS,
Cloudflare policies, email delivery, power settings or off-machine backups work.
Keep registration closed until all launch gates below have evidence.

## Application gates

- [ ] Back up the database and workspace before deploying migrations. Migrations
  apply automatically on app startup; run one app process only.
- [ ] Put the versioned encryption keyring outside `workspace/` and `backups/`,
  mount it as the compose secret, and keep its independent recovery copy in a
  password manager. A DB dump contains encrypted provider keys, never the KEK.
- [ ] Set `APP_ENV=production`, `PDF_REPORT_COOKIE_SECURE=1` and leave
  `PDF_REPORT_ALLOW_UNSAFE_SANDBOX` and `PDF_REPORT_DEV_SERVER_KEYS` disabled.
- [ ] Run `docker compose exec report-generator python -m app.cli launch-check`.
  Every check must pass. This checks local settings and storage; verify the
  external Cloudflare policy and SMTP permissions separately.
- [ ] Run the suite against a separate `_test` DB, with
  `APP_ENV=test MPLBACKEND=Agg PDF_REPORT_REQUIRE_BWRAP=1 python -m unittest discover -s tests`.
  The suite covers route ownership, keys in responses/logs/state, CSRF, cookies,
  rate limits, enumeration, link-safe file serving/cleanup and sandbox isolation. The network test disables the
  Python guard and confirms the namespace cannot connect to the host or Postgres.
- [ ] Run canonical sessions for each enabled provider using a dedicated account.
  See [canonical instructions](../tests/canonical/README.md). The Anthropic,
  OpenAI and OpenRouter live runs still need their owner keys.

## Cloudflare and SMTP

- [ ] Keep the compose app port bound to `127.0.0.1:8000`; do not publish Postgres.
  Set the tunnel's public hostname to `repgen.gengardev.com.br` and its service
  to `http://localhost:8000`. Keep the catch-all ingress returning 404.
- [ ] Enable Always Use HTTPS and HSTS after checking HTTPS for the hostname.
  Set `PUBLIC_BASE_URL=https://repgen.gengardev.com.br`.
- [ ] Bypass cache for `/api/*`, `/session/*`, `/account`, `/keys`, `/usage` and
  `/admin*`. Confirm signed-in responses include `Cache-Control: private, no-store`.
- [ ] Add an outer WAF rate limit for `/api/auth/*`, accounting for normal login,
  signup and reset traffic. Keep the application's IP and account limits enabled.
- [ ] Configure Turnstile for the production hostname and set both site and secret
  keys. Check successful and rejected challenges through the public hostname.
- [ ] Create one Access self-hosted application covering `/admin*` and
  `/api/admin/*`, with an allow policy limited to operators. Use the same application
  audience for both paths. Configure `CLOUDFLARE_ACCESS_ISSUER` as
  `https://YOUR-TEAM.cloudflareaccess.com` and `CLOUDFLARE_ACCESS_AUD` as its AUD tag.
  Admin endpoints verify RS256 signature, expiration, issuer, audience and the
  email matching the signed-in admin. Production admin access fails closed if
  configuration is absent. A header's presence alone does not grant access.
- [ ] Confirm an ordinary user gets 403, a missing/invalid assertion gets 403, and
  an allowed operator with an app admin account can use both the page and API.
- [ ] Restrict trusted proxy networks to the actual Docker gateway. Confirm
  `CF-Connecting-IP` comes from that gateway and direct LAN access is closed.
- [ ] Use a transactional SMTP credential with send-only permissions. Scope it
  to the sending domain if supported. Set `SMTP_FROM`; use TLS on port 465 or
  STARTTLS on 587. Check SPF, DKIM and DMARC and deliver verification and reset
  mail to a real inbox. App configuration cannot inspect provider permissions.

Cloudflare's [JWT validation documentation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)
describes the issuer certificate endpoint and audience validation used here.

## Capacity and Mac host

Run this inside the same Linux VM/container image as production, against a
**disposable** database and workspace. The test DB is emptied at process startup.
Do not point the command at production and do not run it alongside unit tests.

```bash
APP_ENV=test MPLBACKEND=Agg PDF_REPORT_REQUIRE_BWRAP=1 python -m tests.load_check --users 10
```

It starts 5–10 distinct owners simultaneously, uses the real job slots, writes
Postgres session state, and generates sandboxed PDFs without billable API calls.
Record elapsed time, peak jobs, failure count and VM memory. Then run live
canonical jobs to measure actual provider latency and report complexity.

- [ ] Verify bubblewrap inside the Mac's Linux container.
- [ ] Start with `PDF_REPORT_MAX_CONCURRENT_JOBS=2` and
  `PDF_REPORT_MAX_JOBS_PER_USER=1`. Allow about 2 GB per sandbox plus the app,
  Postgres and the VM. Choose VM memory and global slots from the Mac measurements.
- [ ] Inspect Postgres connections during the load check; defaults are pool size
  10 plus overflow 10. Avoid raising slots or pool size without measuring memory
  and Postgres connection capacity.
- [ ] Disable host sleep while serving and enable restart after power failure.
- [ ] Configure Docker and cloudflared to start automatically after reboot.
  Compose uses `restart: unless-stopped` for both services. Verify with a reboot.
- [ ] Configure tunnel unhealthy notifications to an operator and test delivery.
  Use an independent HTTPS uptime check; a notification from this Mac cannot
  report its own power loss. Consider a UPS.

## Backup and restore gate

1. Drain jobs and use a maintenance window for a consistent DB/workspace pair.
   Run `BACKUP_SYNC_COMMAND='YOUR_OFF_MACHINE_COPY_COMMAND' scripts/backup.sh`.
   Backup files are created with private permissions. Retention defaults to 14 days.
2. Verify that the off-machine destination has both files with the same timestamp.
   Download them to a fresh folder; do not use the still-local originals as evidence
   of off-machine recovery. Keep the encryption keyring separate.
3. Rehearse without touching the app or the live workspace:

   ```bash
   python scripts/restore-drill.py /downloaded/db-TIMESTAMP.dump /downloaded/workspace-TIMESTAMP.tar.gz
   ```

   Requires Docker, Python 3.12+ and the local `postgres:17.6-alpine` image. It
   restores to an ephemeral Postgres container with no network or published ports,
   extracts the archive into a temporary folder, checks table counts and referenced
   files/PDF headers, then removes the container and temporary files. It rejects
   traversal and links in archives. It does not decrypt provider credentials.
4. On an isolated staging deployment, mount the separately recovered KEK and
   confirm a stored credential decrypts and an imported report opens and renders.
   Never send test mail or live model calls from a restored production snapshot
   without deliberately configuring the staging services.
5. Record backup timestamp, drill date, result, duration, missing artifacts and the
   operator. Establish a recovery-time target and repeat the drill regularly.

## Retire legacy folders

Production cannot mount shared flat folders. New reports already use owner roots.
With the app stopped, import JSON reports if any remain, then migrate DB reports:

```bash
docker compose stop report-generator
docker compose run --rm report-generator python -m app.cli import-legacy --owner demo@example.com --dry-run
docker compose run --rm report-generator python -m app.cli import-legacy --owner demo@example.com
docker compose run --rm report-generator python -m app.cli migrate-storage --dry-run
docker compose run --rm report-generator python -m app.cli migrate-storage
docker compose up -d report-generator
docker compose exec report-generator python -m app.cli launch-check
```

The migration locks each report, copies files, rewrites paths, preserves owners
and refuses reports with missing source files. Originals remain for recovery.
After reviewing imported reports and rehearsing restore, archive and remove the
old `uploads`, `filtered`, `scripts`, `reports` and `sessions` folders from the
deployment workspace. Do not remove `tenants` or `sandbox_support`.
Development/test-only legacy fixture support remains until all deployments have
confirmed import completion; it is unreachable by production sandboxes.

## Evidence recorded in this checkout

Implementation tests and local drills are recorded in the plan's Phase 6/7 notes.
The Mac mini load run, Cloudflare dashboard settings, actual SMTP permission scope,
downloaded off-machine restore and final legacy-folder retirement require host
access and remain release gates until an operator records their results here.
