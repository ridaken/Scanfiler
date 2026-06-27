# scanfiler

[![CI](https://github.com/ridaken/Scanfiler/actions/workflows/ci.yml/badge.svg)](https://github.com/ridaken/Scanfiler/actions/workflows/ci.yml)

AI-powered renamer / reorganizer for a dump of scanned documents. It walks an inbox
**one file at a time**, sends the first page(s) to an OpenAI-compatible vision-language
model (built for [llama.cpp / llama-server](https://github.com/ggml-org/llama.cpp) and
mlx-vlm; cloud providers work too), and gets back a meaningful filename, a target
subfolder, and RAG-ready metadata (doc type, date, summary, tags). By default it
**proposes** changes to a review file you approve before anything moves.

```
walk inbox → skip already-seen (by content hash) → extract (PDF/docx/image)
   → AI decides {filename, subdir, doc_type, date, summary, tags, confidence}
   → sanitize + resolve collisions → write proposals  ──review──▶  apply (copy/move) + sidecars
```

## Why an inbox separate from the library

The scan dump is typically an `rclone` **one-way pull** of Google Drive — a *mirror*.
If you renamed files in place, the next sync would delete them (they're gone from Drive)
and re-download the originals, undoing your work in a loop. So `scanfiler` treats the
inbox as **read-only** and **copies** organized files into a separate `library_dir` that
rclone never touches. A SQLite ledger keyed by **content hash** means a re-downloaded
`SCAN0001.pdf` is recognised as already-processed and skipped.

## Install

```bash
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

scanfiler init          # writes config.yaml (won't overwrite)
# edit config.yaml: paths.inbox_dir / library_dir, ai.base_url / model
```

Secrets are `${ENV_VAR}` references read from the environment, never stored in the file:

```bash
export AI_API_KEY=''    # usually empty for local llama-server
```

## Usage

```bash
scanfiler plan                 # extract + AI for new files -> proposals.jsonl (no moves)
# review/edit proposals.jsonl (fix a name, change a subdir, or delete a line to skip)
scanfiler apply                # copy/move per the (edited) proposals, writing sidecars

scanfiler run                  # one cycle; auto-applies if apply.mode=auto, else proposes
scanfiler loop                 # daemon on scheduler.polling_minutes (macOS/Windows)

scanfiler status               # ledger counts
scanfiler undo --last          # reverse the most recent apply run
scanfiler <cmd> --dry-run      # decide + log, never touch disk
```

## Try it on the bundled samples

The repo ships three sample inputs in `samples/inbox/` (an auto-service receipt PDF,
an electrician's invoice docx, and a child's crayon drawing PNG) and a ready-to-run
`samples/config.yaml`. Point `ai.base_url` at a vision model, then:

```bash
scanfiler -c samples/config.yaml plan --proposals samples/proposals.jsonl
# review samples/proposals.jsonl, then:
scanfiler -c samples/config.yaml apply --proposals samples/proposals.jsonl
# results land in samples/library/ (gitignored); undo with:
scanfiler -c samples/config.yaml undo --last
```

The drawing has no text, so it exercises the low-confidence → `_Unsorted` path.
Regenerate the samples any time with `python samples/generate_samples.py`.

## Key config

| Key | Meaning |
| --- | --- |
| `paths.inbox_dir` / `library_dir` | Read-only mirror in, tool-owned library out (keep separate). |
| `extraction.pdf_max_pages` / `send_mode` | First N pages; `vision`/`text`/`auto`. |
| `selection.process_pattern` / `process_all` | Which files "look unprocessed"; set `process_all` to rename everything. |
| `selection.min_mtime_age_s` | Skip files still being written/synced. |
| `naming.allow_new_subdirs` | If false, proposed new folders are shunted to `_Unsorted` for review. |
| `categorization.confidence_threshold` | Below this → `_Unsorted`, never a guess. |
| `apply.mode` / `action` | `review`/`auto`; `copy` (pristine inbox) / `move`. |
| `rag.write_sidecar` | Write `<name>.json` metadata next to each organized file (future RAG corpus). |

## Safety

- Model output is never trusted raw: invalid chars, Windows reserved names, length,
  path traversal, and extension hijacking are all sanitized (`scanfiler/naming.py`).
- Collisions are resolved against both existing files and other proposals in the batch.
- Every move is recorded in `logs/audit.jsonl` and reversible with `undo`.
- A lockfile prevents overlapping cron/timer runs from double-processing.
- Local models keep medical/PII documents off the cloud.

## Deployment (Debian)

```bash
# one cycle, on a systemd timer — see systemd/
sudo cp systemd/scanfiler.service systemd/scanfiler.timer /etc/systemd/system/
sudo systemctl enable --now scanfiler.timer
journalctl -u scanfiler.service -f
```

macOS/Windows: use `scanfiler loop` under launchd / Task Scheduler.

## Self-update

With `auto_update.enabled: true`, each run first checks the git remote and, if a newer
version exists, updates and re-runs on it before doing any work:

```
git fetch → pick target (latest release tag by default, or a branch)
   → if newer & working tree clean: checkout → pip install (if pyproject.toml changed)
   → re-exec into the new version (so this run uses it)
```

- **`ref`** — `latest-release` (newest `vX.Y.Z` tag; stable, recommended) or a branch
  name like `main` (bleeding edge).
- **Signature verification (on by default).** Before applying, the target commit's
  signature is checked with `git verify-commit`. If it isn't validly signed by a trusted
  key, the update is **refused** (fail-closed) and the run continues on the current
  version. Disable with `verify_signature: false` only if you understand the risk. For
  SSH-signed commits, point `allowed_signers_file` at an allowed-signers file; for GPG,
  import your public key into the deploy user's keyring and leave it unset.
- **Fail-safe.** Any problem (offline, dirty tree, install error) is logged and the run
  continues on the current version; a failed install rolls the checkout back.
- **Skipped** when not run from a git clone, when the working tree is dirty, and on
  `--dry-run`. In `loop` mode the check runs each cycle, so a long-running daemon picks
  up releases without a manual restart (it tracks tags via a detached HEAD).
- **Requires the deploy to be a git working tree** with network access to the remote —
  i.e. `git clone` the repo and `pip install -e .` rather than installing a wheel. The
  `repo_dir` defaults to the repo root above the package; override it if needed.

## Testing

```bash
pytest          # stubs the AI client and generates sample PDFs/images; nothing external needed
ruff check .    # lint
```

`pytest` enforces a coverage floor (`--cov-fail-under=90` in `pyproject.toml`).

## Contributing & releases

Changes land via **feature branch → pull request → merge into `main`**, not direct
commits to `main`.

```bash
git checkout -b my-change
ruff check . && pytest
git push -u origin my-change
gh pr create --base main --fill
```

CI (`.github/workflows/ci.yml`) runs the gates on every PR and push: **ruff lint**,
**pytest + coverage gate**, and a **package build**, across Python 3.11/3.12/3.13.
On a push to `main` that passes all gates, the release job auto-increments the patch
version, tags it (`vX.Y.Z`), and publishes a GitHub Release with generated notes — so
direct commits to `main` would make those notes noisy; use PRs.
