"""Embedded scaffolding templates (config) used by `scanfiler init`."""

DEFAULT_CONFIG = """\
# scanfiler configuration.
# Copy to config.yaml and edit. Secrets use ${ENV_VAR} interpolation so they are not
# stored in plaintext here — set those variables in the environment (or a systemd unit
# / .env loaded by your service manager).

paths:
  inbox_dir: /data/gdrive/ScanDump      # rclone mirror, treated READ-ONLY
  library_dir: /data/Library            # tool-owned destination (NOT rclone-synced)
  unsorted_subdir: _Unsorted            # low-confidence / extraction-failure landing

ai:
  base_url: http://localhost:8080/v1    # OpenAI-compatible (llama-server / mlx-vlm / cloud)
  api_key: ${AI_API_KEY}                # often unused for local llama-server
  model: local-vlm                      # model name the server expects
  temperature: 0.2
  request_timeout_s: 90
  max_retries: 3
  constrained_output: true              # force schema-valid JSON output

extraction:
  pdf_max_pages: 2                      # only the first N pages go to the model
  raster_dpi: 150                       # rasterization DPI for the vision model
  send_mode: auto                       # vision | text | auto. text = never send images
                                        #   (image-only files are skipped); auto uses text
                                        #   when the layer is rich, else page images.

selection:
  process_pattern: '^(SCAN|PIC|IMG)[\\W_]*\\d+'   # "looks unprocessed"
  process_all: false                    # true = also rename already-named files
  ignore_globs: ['*.partial', '.*']     # skip rclone temp / hidden files
  min_mtime_age_s: 30                    # skip files modified within the last N seconds (mid-sync)

naming:
  date_prefix: true                     # ISO 'YYYY-MM-' filename prefix for sortability
  max_filename_len: 120
  allow_new_subdirs: true               # if false, proposed new folders -> _Unsorted

categorization:
  confidence_threshold: 0.6             # below this -> _Unsorted (never a guess)

rag:
  write_sidecar: true                   # write <name>.json metadata next to each file

scheduler:
  mode: once                            # once (systemd timer/cron) | loop
  polling_minutes: 15                   # used by `scanfiler loop`

apply:
  mode: review                          # review (propose only) | auto (move on each run)
  action: copy                          # copy (leave inbox pristine) | move
  on_collision: suffix                  # suffix (-2,-3) | skip | overwrite

logging:
  enabled: true                         # write a fresh timestamped log file each run
  log_dir: ./logs                       # where per-run logs land (scanfiler-<timestamp>.log)
  level: info                           # debug | info | warn | error
  audit_file: ./logs/audit.jsonl        # JSONL record of every move (reversible via undo)
  ledger_db: ./state/ledger.sqlite      # content-hash processed-file ledger

auto_update:
  enabled: false                        # check for + apply a newer version before each run
  ref: latest-release                   # newest vX.Y.Z tag, or a branch name (e.g. main)
  install_deps: true                    # reinstall (pip install -e .) when pyproject.toml changed
  restart: true                         # re-exec into the new version so this run uses it
  verify_signature: true                # require a trusted commit signature (fail-closed)
  # allowed_signers_file: /etc/scanfiler/allowed_signers  # SSH-signed commits; omit for gpg keyring
  # repo_dir: /opt/scanfiler            # defaults to the repo root above the package

prompt:
  context: >
    These are personal scanned documents: receipts, invoices, medical records,
    letters, kids' drawings, and similar. Most are named generically (SCAN0001).
"""
