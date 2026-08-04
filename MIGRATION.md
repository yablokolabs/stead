# VM migration and disaster recovery

The Git repository is the reproducible **code and configuration source**. It
contains the locked Python environment, profile renderer, identity, skill, MCP
server, systemd installer, verifier, and migration tooling.

It intentionally does **not** contain credentials or private household state.
Those move in one mode-`0600` private bundle created by `export-state.sh`.
Never commit or attach that bundle to an issue, chat, or public release.

## What the private bundle contains

- `~/.stead-demo/.env` — Telegram/model credentials and routing IDs
- `~/.stead-demo/stead.sqlite` — household facts, tasks, proposals, reminders,
  outcomes, and audit events, captured with SQLite's online backup API
- the Stead profile's canonical `state.db`, memories, session artifacts, cron
  jobs/execution database, and Telegram routing state
- a manifest containing the source Git commit and SHA-256 for every payload

It excludes generated profile configuration, identity, skills, logs, caches,
Hermes binaries, `auth.json`, and SearXNG configuration. Those are recreated
from the repository or the Hermes installation on the new VM.

The bundle is private but not encrypted by this repository. Transfer it only
over an encrypted channel such as `scp`, or put it in an encrypted S3 bucket
with public access blocked and SSE-KMS enabled.

## 1. Export on the old VM

From the current checkout:

```bash
cd /home/azureuser/yablokolabs/stead-preview
./scripts/export-state.sh
```

The script briefly stops only the Stead service, creates consistent SQLite
snapshots, restarts Stead, and prints the bundle path. The default is:

```text
~/stead-backups/stead-private-<UTC timestamp>.tar.gz
```

Copy the bundle **off the old VM** before deleting it. Example:

```bash
scp ~/stead-backups/stead-private-*.tar.gz \
  NEW_AWS_VM:/home/NEW_USER/
```

Do not delete the old VM yet.

## 2. Prepare the new AWS VM

Use a Linux image with systemd. Install `git`, Docker only if web search is
wanted, and the current Hermes Agent release from the official documentation:

<https://hermes-agent.nousresearch.com/docs>

The Hermes installer provides `uv`. Clone the private repository wherever the
new user keeps source code:

```bash
git clone https://github.com/yablokolabs/stead-preview.git
cd stead-preview
chmod 600 /home/NEW_USER/stead-private-*.tar.gz
```

For a user service that survives SSH logout and starts after reboot, enable
lingering once (requires administrator access):

```bash
sudo loginctl enable-linger "$USER"
```

## 3. Restore and start

```bash
./scripts/bootstrap-vm.sh \
  --restore /home/NEW_USER/stead-private-<timestamp>.tar.gz
```

This command:

1. recreates `.venv` exactly from `uv.lock`;
2. creates the isolated `stead-kerstin-demo` profile without cloning another
   profile's credentials;
3. restores the private bundle and verifies every checksum/SQLite database;
4. regenerates profile paths for the new home and checkout;
5. installs the user-level Hermes service and the credential-enforcing drop-in;
6. recreates the pinned loopback-only SearXNG container when web search is
   configured;
7. runs the launcher fail-closed gate, starts Stead, and runs the verifier.

The repository may live at a different absolute path on the new VM. Setup
renders that path into `config.yaml` and the systemd drop-in; no old-VM path is
restored.

## 4. Acceptance checks

All of these must succeed on the new VM:

```bash
systemctl --user is-active hermes-gateway-stead-kerstin-demo
./scripts/verify.sh
hermes --profile stead-kerstin-demo cron list
```

Then send Stead a benign Telegram message and create one real reminder through
the normal proposal/approval flow. Confirm delivery before terminating the old
VM.

## The nightly fleet backup is a second, unattended copy

`export-state.sh` is deliberate and operator-driven: you run it when you are
about to move VMs. It is not a schedule, so between migrations nothing is
protected.

The host's fleet backup covers that gap. `~/yablokolabs/bots_soul/backup-bots.sh`
runs nightly at 21:30 UTC under `bots-backup.timer` and uploads one GitHub
release per run to the private `yablokolabs/bots-vault`. Two of its archives
carry Stead:

- `hermes-state.tar.zst` — all of `~/.hermes`, which includes the
  `stead-kerstin-demo` profile: memories, sessions, cron jobs and `state.db`
- `stead-demo-state.tar.zst` — all of `~/.stead-demo`: the household database
  and the protected env file

Each SQLite database is snapshotted with `VACUUM INTO` rather than copied, then
extracted and integrity-checked before the release is created; a database that
will not restore aborts the run instead of shipping.

Two things this is **not**:

- **It is not encrypted.** The vault repository holds plaintext credentials by a
  deliberate decision recorded in `backup-bots.sh`, after an encrypted scheme
  lost its key in July 2026 and produced five days of unreadable backups. Read
  access to `bots-vault` is therefore equivalent to holding Stead's Telegram
  token, model key and Kerstin's household history. Restrict it accordingly — it
  now contains a third party's personal data, not only your own bot credentials.
- **It is not a substitute for this document.** The archives are host state, not
  a rendered deployment. A restore still needs this repository cloned and
  `setup.sh` run to regenerate profile paths for the new host. Use
  `export-state.sh` for a planned migration; use the vault when the VM is gone
  and there is no export to hand.

## Fresh install without old private state

Run:

```bash
./scripts/bootstrap-vm.sh --no-start
cp .env.example ~/.stead-demo/.env
chmod 600 ~/.stead-demo/.env
$EDITOR ~/.stead-demo/.env
./scripts/setup.sh --start
./scripts/verify.sh
```

This creates a new empty household; it does not recover old facts, sessions, or
credentials.

## Safety and rollback

- `restore-state.sh` requires a mode-`0600` archive, rejects links/path
  traversal, duplicate or unexpected members, and archive size-limit breaches;
  it validates exact file inventory, checksums, and SQLite integrity.
- Every incoming file/tree is staged before the live state is touched. The
  managed environment, databases and sidecars, memories, sessions, cron state,
  and routing files are committed together and rolled back together on failure.
  If post-commit deletion of hidden rollback artifacts fails, restore remains a
  success, restarts the previously active service, and emits an explicit warning
  for operator cleanup rather than misreporting a partial restore.
- Restore never imports profile `config.yaml`, `SOUL.md`, skills, binaries,
  caches, logs, or `auth.json`; tracked assets are regenerated instead.
- Keep the old VM stopped but recoverable until Telegram access, reminder
  creation/delivery, database state, and service boot persistence pass on AWS.
- After acceptance, remove the private bundle from transient locations or move
  it into your encrypted long-term backup system.
