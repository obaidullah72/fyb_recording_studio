# Hostinger deployment

The Git root is `frontend` itself (`git rev-parse --show-toplevel`), so the publish
source is `.`. This is HTML, CSS, and vanilla JavaScript: no npm install or build.
There were no existing workflows to replace. Production is `main`.

`.github/workflows/deploy-hostinger.yml` runs on pushes to `main` and through
**Actions → Deploy static site to Hostinger → Run workflow → main**. Manual runs
on other branches are skipped. Only `contents: read` token permission is granted.
Checkout is pinned to the reviewed
[actions/checkout v4.3.1 commit](https://github.com/actions/checkout/commit/34e114876b0b11c390a56381ad16ebd13914f8d5),
with credential persistence disabled. No other external action runs.

## First run

1. Ensure SSH is enabled for this Hostinger account and the deployment key's public
   key is authorized. The server needs **Python 3.8+**, a writable existing website
   directory, and enough account storage for uploads and retained backups. The
   runner uses Python 3 and OpenSSH already supplied by Ubuntu; no packages are
   installed. rsync is preferred when present on both ends; otherwise OpenSSH's
   SFTP-based `scp` is used. An actual rsync failure fails the run rather than
   silently trying another transport.
2. Use the seven **existing Repository secrets** below. No variables, GitHub
   environment, duplicated settings, or additional secrets are required.
3. Confirm that `SITE_URL` is the canonical HTTPS URL for `HOSTINGER_DEPLOY_PATH`,
   including a path suffix if publishing below the domain root. The destination
   must already exist. Its contents receive `index.html` directly, with no extra
   `frontend` folder. A unique temporary file is fetched through `SITE_URL` to
   prove the path mapping before application files change. The probe is removed
   at completion or recovery.
4. Commit these files and push to `main`. That push starts deployment. Inspect the
   Actions log and summary for the verified SHA and recovery release ID.

| Repository secret | Expected value |
| --- | --- |
| `HOSTINGER_DEPLOY_PATH` | Absolute website directory from Hostinger, such as `/home/account/domains/example.com/public_html`; never `/`, the account home, or a broad parent directory |
| `HOSTINGER_KNOWN_HOSTS` | Trusted OpenSSH known_hosts entries, including `[hostname]:port` for a nonstandard port; obtain/verify the fingerprint independently through Hostinger before storing it |
| `HOSTINGER_SSH_HOST` | Hostname or IPv4 address only, without `ssh://` or a port |
| `HOSTINGER_SSH_PORT` | Numeric SSH port |
| `HOSTINGER_SSH_PRIVATE_KEY` | Complete multiline, unencrypted deployment private key, matching the authorized public key |
| `HOSTINGER_SSH_USER` | Hostinger SSH username |
| `SITE_URL` | Canonical HTTPS public base URL, without query string, fragment, or embedded credentials |

Required values are checked without logging their contents. The key and
known_hosts are temporary files with mode `600` in a private directory. SSH uses
strict host verification, no interactive authentication, and only the configured
key/host keys. SSH and transfer diagnostics are suppressed because they can
contain secrets; failures still return a failing exit status.

## What is published and checked

The packaging script reads **Git blobs at the exact checked-out SHA**, so neither
a moving branch tip nor uncommitted files can enter the deployment. The current
allowlist publishes 42 files: `index.html`, `404.html`, `app.js`, `styles.css`,
`asset-manifest.json`, `verification.json`, and public files within `assets/`,
`home/`, `hello-world/`, and `category/`. It also generates `deployment.json`,
containing the deployed SHA and every application file's SHA-256 hash.

`scripts/verify.mjs` is optional Playwright QA tooling; all of `scripts/` stays off
the server's public directory. `.git`, `.github`, dotfiles, credentials, local
environment files, backup extensions, and documentation are excluded. Keep
credentials outside public assets. When adding a new top-level page directory or
asset type, update `PUBLIC_DIRS` / `PUBLIC_EXTENSIONS` in the packaging script.
Runtime JS remains `app.js`; the current JS has no fetched/imported dependencies.
If that changes, extend validation for those new runtime dependencies.

Validation checks the homepage, local HTML references (including `<base>`, source
sets, posters, and links), CSS URLs/imports, and all original asset sizes/hashes.
Changes to migrated assets must update `asset-manifest.json`. `verification.json`
is the historical browser-QA report, not proof of the current live deployment.

After upload, the server checks the archive and every file's hash. It then saves
the previous contents of the union of old/new managed paths, including any files
that the first deployment will replace. Only after a complete snapshot does it
install files. Existing symlinks or directory/file conflicts in managed paths
fail safely. The deployment preserves unrelated files such as `.htaccess`,
`.well-known`, uploads, and PHP files. Only previously managed files that disappear
from a later release are removed; there is **no rsync `--delete`**.

Public verification GETs every packaged file, including the video and commit
record, and checks its actual content hash. Directory indexes are checked at `/`,
`/home/`, `/hello-world/`, and `/category/uncategorized/`; `/404.html` is also checked.
HTTP errors, redirect loops (maximum five redirects), foreign-origin redirects,
TLS failures, timeouts, and wrong content all fail deployment. Cache-busting and
no-cache headers are used. Disable response rewriting/minification for these
files, and ensure any CDN respects query-string cache busting; otherwise exact
content verification deliberately fails.

Existing routing rules remain untouched. The host must serve these real static
files and directory indexes. A WordPress rewrite, preferred `index.php`, or login
redirect that intercepts them will fail verification. No SPA fallback, WordPress
configuration, or custom missing-page routing is installed. Configure `404.html`
as the host's error document separately if desired.

## Recovery

Deployments share one GitHub concurrency group with `cancel-in-progress: false`.
An active transfer is never cancelled by a newer push. With GitHub's default
[concurrency behavior](https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency),
newer pending runs can replace an older pending run. A separate server lock also
blocks concurrent writers and blocks further deployments after a hard interruption.

Uploads, checksummed backups, snapshots, and status records live outside the
website in `$HOME/.hostinger-deploy/<destination-hash>/<release-id>/`, mode `700`.
The parent directory contains `current.json`, `last-success.json`, and an active
`lock/owner` while deploying. Each release contains `backup/` and `ready.json`
when its snapshot is complete. Backups are retained without automatic pruning;
monitor disk space and remove obsolete release directories only after confirming
they are neither locked nor the latest successful recovery snapshot.

Transfer/activation/health-check failures trigger automatic restoration and
checksum verification of the prior managed files, then fail the workflow. A
failure before activation leaves the live application unchanged. Individual
files are installed by rename, but the entire multi-file website switch is not
atomic: visitors may briefly see mixed versions during activation/recovery.
Do not manually edit managed files during a run.

For an ordinary rollback, revert the offending commit on `main` and push. This
creates a fresh, fully validated deployment of the desired source.

For an interrupted run or immediate restoration of the previous server copy:

1. Stop new pushes and ensure the old Actions run/SSH transfer has actually ended.
   Record the **Recovery release ID** printed near the start of that run (also
   present in the summary for a successful run).
2. On a trusted machine with Python **3.9+**, OpenSSH and these scripts, supply the
   same seven secrets as environment variables through your secret manager. Do
   not put key material in command lines or tracked files. Run:

   ```sh
   python3 scripts/deploy_hostinger.py recover --release RELEASE_ID_FROM_LOG
   ```

   This restores that run's pre-deployment snapshot and verifies restored file
   hashes. It accepts the active lock owner or the latest successful release; an
   older release cannot overwrite a newer healthy deployment. An incomplete
   upload is simply unlocked without changing application files. It removes only
   newly introduced managed files, preserving unrelated server contents.
3. Verify the homepage, page routes, and assets publicly before resuming pushes.
   If the restored version is a known commit, check out that commit in a separate
   directory, package it with the current packaging script, and use the `health`
   command below against its package. First-run backups may predate this repo and
   require manual browser checks.

If SSH is unreachable, reconnect through Hostinger and inspect the private state
directory above. Do not delete `lock` blindly or extract a backup over the entire
website. Repair access/storage, then rerun recovery using the lock owner's release
ID. If the original process died before creating its release directory, confirm
no process is running and inspect the lock before removing that abandoned lock.

## Local validation

From the repository root, use a new temporary output directory each time:

```sh
python3 -m unittest discover -s scripts -p 'test_deploy_hostinger.py' -v
python3 scripts/deploy_hostinger.py package --source . --output /tmp/hostinger-package
# With SITE_URL exported, verify an already-deployed matching version:
python3 scripts/deploy_hostinger.py health --output /tmp/hostinger-package
```

Tests use isolated temporary directories and localhost HTTP servers. They cover
packaging, missing/corrupt assets, nested source directories, upload corruption,
unsafe archives/symlinks, serialization, interrupted activation, managed-only
removal, rollback, and HTTP failures/redirects. They do not access Hostinger or
Repository secret values. Live SSH, hosting prerequisites, DNS/TLS/CDN behavior,
and a real deployment remain untested until a successful Actions run.
