# Repository instructions

Use isolated synthetic data for tests. Never commit real reports, accounts, credentials, databases, server settings or private logs.

Run `python -m ops.check` for source changes. For UI changes, also run the isolated browser checks documented in `ops/README.md` and inspect desktop and mobile results. Preserve existing API and data compatibility; destructive migrations require explicit authorization.

Deploy only when a maintainer explicitly requests it, and follow the deployment's own configuration and data-preservation requirements. See `ops/README.md` for setup and verification.
