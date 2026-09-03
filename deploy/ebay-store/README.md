# Private eBay evaluation store

The batch CLI remains the normal application. This optional service is the one
retained eBay-data destination; it is not the Polymarket application.

## Deployment scope

- Host: Alwyzon, application directory `/opt/bazar-deals`.
- Public HTTPS: `https://46-102-157-230.sslip.io/`; ports 80/443 for HTTPS and ACME renewal.
  The direct IP endpoint remains available for operational checks, but eBay's
  notification tester requires a DNS name in the certificate.
- Private application port 8090 is reachable only inside the Compose network.
- Existing Polymarket service on `127.0.0.1:8080` is unchanged.
- Two containers, memory limits 256 MiB and 192 MiB, restart unless stopped.
- Caddy obtains a public short-lived IP certificate and renews it automatically.
  [Let's Encrypt IP certificates](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability.html).

The production start/firewall change requires the owner's explicit approval.
Preparing the files or validating Caddy is not a completed deployment.

## Secrets and activation sequence

Create an ignored `deploy/ebay-store/.env`, permission 0600, with:

```text
EBAY_STORE_TOKEN=<cryptographically random access secret, at least 32 characters>
EBAY_VERIFICATION_TOKEN=<random 32–80 alphanumeric/underscore/hyphen characters>
EBAY_NOTIFICATION_URL=https://46-102-157-230.sslip.io/ebay/account-deletion
EBAY_STORE_DIR=/data
```

Prepare `data/` owned by UID 10001, permission 0700. After deployment approval,
start with `docker compose -f deploy/ebay-store/compose.yml up -d --build`.
Check public TLS without disabling certificate verification, `/health`, rejected
unauthenticated `/api/status`, and the challenge hash before registering eBay.

Set GitHub secret `EBAY_STORE_TOKEN` and variable `EBAY_STORE_URL` to the HTTPS
origin. The manual **eBay stored evaluation** workflow with `configure_only=true`
transfers existing eBay credentials over authenticated HTTPS into an encrypted
server file. Neither the workflow nor service logs the secrets.

Only once the endpoint works: disable the no-persistence exemption in the eBay
developer console, set the outage contact email, endpoint and verification token,
save, and send eBay's test notification. The test must be signature-verified and
acknowledged successfully. Then call authenticated `POST /api/enable` and set
repository variable `EBAY_STORE_ENABLED=true`. Run the evaluation manually and
verify the private dashboard; subsequent collection runs hourly at minute 45.

Do not enable the ordinary CLI's `EBAY_RETENTION_ENABLED`: those paths can still
export data into local files or GitHub reports outside this deletion service.

## Retention and deletion

- Retain eBay snapshots only here, encrypted using Fernet; no raw API payloads,
  third-party AI exports, GitHub comments, artifacts or comps-cache entries.
- Keep snapshots seven days, with a 15-minute expiration sweep. Reading or writing
  also expires old rows. Do not configure backups/snapshots of this data volume.
- Dashboard requires the access token; responses use `Cache-Control: no-store`,
  escaped HTML, restricted eBay item URLs and secure HttpOnly cookies.
- Verify notifications against eBay's official public-key endpoint, cache keys
  for at most one hour, and reject missing/invalid signatures. The SHA-1 signature
  algorithm matches [eBay's official SDK](https://github.com/eBay/event-notification-nodejs-sdk/blob/master/lib/constants.js).
- Every new, verified account-deletion event deletes **all eBay snapshots**.
  This conservative initial policy intentionally discards unrelated comparisons
  too, avoiding partial deletion of aggregate/derived records. It does not touch
  any other marketplace's data or the checked-in owner-provided stock catalog.
- SQLite secure deletion, rollback-journal mode and VACUUM remove payloads from
  application-managed storage. Only keyed hashes of deleted identities/event IDs
  remain, to reject re-imports and repeated deliveries. In-flight batches are
  rejected using a monotonically increasing epoch.
- A storage or key-fetch failure returns a non-success status so eBay retries.
  Monitor endpoint availability and the configured eBay failure email. Host-level
  backups, if enabled separately, need their own deletion policy before storing
  eBay data; they are not managed by this application.

The dashboard distinguishes active stock comparisons, unreviewed purchase
candidates and actual want-to-buy titles. An active listing is neither a sold
price nor a confirmed buyer; requesting `deliveryCountry:SK` alone does not turn
a candidate into an approved BUY.

## Rollback

Set `EBAY_STORE_ENABLED=false` in GitHub first to stop new scheduled collection.
Keep the deletion receiver available while stored data remains. Delete all
snapshots through the maintenance process before shutting down the service or
re-applying a no-persistence exemption. Never disable the existing tracker.
