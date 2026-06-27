# scanfiler

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

## Testing

```bash
pytest          # stubs the AI client and generates sample PDFs/images; nothing external needed
```
