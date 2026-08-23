# Sa3arly Product-Centric Catalog and Price Engine 0.6.1

Production backend for Egyptian catalog discovery, cash-price comparison, and
installment comparison on Cloud Run, Cloud Tasks, Cloud Scheduler, and Cloud
SQL for PostgreSQL.

- Price refresh: 10:00 and 20:00 `Africa/Cairo` (`0 10,20 * * *`).
- Catalog discovery: 02:30 `Africa/Cairo` (`30 2 * * *`).
- Registry: 216 stores; 209 active; 14 currently mapped; 195 active stores
  waiting for progressive discovery.
- Discovery batch: 35 stores nightly, covering the first pass in about six
  nights and rescanning successful sources weekly.

Only high-confidence matches create live mappings. Ambiguous candidates stay in
a review queue. A newly discovered GTIN remains hidden until a second store
corroborates it. Robots rules, host allowlists, SSRF protection, response-size
limits, and per-store rate limits remain mandatory.

## Autonomous GitHub and Google Cloud rollout

The repository includes GitHub Actions for verification, Cloud SQL backup,
checksum-pinned migrations, API/worker deployment, rollback, and an isolated
Next.js Cloud Run web preview. The one-time bootstrap is:

```bash
SA3ARLY_ADMIN_EMAILS="YOUR_ADMIN_EMAIL" \
  bash scripts/bootstrap_autonomous_delivery.sh
```

The new `sa3arly-web` service does not change the current domain mapping. See
`GITHUB_AUTOMATION_SETUP_AR.md` for the account-level GitHub App step.

## Verification

```bash
python scripts/verify_catalog_discovery_readiness.py
ruff check app scripts tests
pytest
python -m compileall -q app scripts tests
```

See `GITHUB_AUTOMATION_SETUP_AR.md`, `RELEASE_NOTES_V0_6_1_AR.md`, and
`VALIDATION_REPORT_V0_6_1_AR.md` for the complete operating guide.
