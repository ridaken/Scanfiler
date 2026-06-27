"""Per-run logging: event lines, summary counts, and failure detail."""

from __future__ import annotations

from scanfiler.ai.client import AIError
from scanfiler.ledger import Ledger
from scanfiler.pipeline import plan
from scanfiler.runlog import open_run_log


def test_log_records_events_and_summary(config, stub_client, workspace):
    with open_run_log(config) as (log, path), Ledger(config.logging.ledger_db) as ledger:
        proposals, stats = plan(config, stub_client, ledger, log)

    text = path.read_text(encoding="utf-8")
    assert "run start" in text
    assert "SEND:" in text
    assert "OK:" in text
    # TaxReturn2024.pdf doesn't match process_pattern -> counted + logged as a criteria skip.
    assert "SKIP (criteria): TaxReturn2024.pdf" in text
    assert stats.skipped_selection >= 1
    # summary block with the user-requested counts
    assert "run summary" in text
    assert "sent to LLM:" in text
    assert "processed successfully:" in text
    assert "failed:" in text
    assert "skipped (already judged):" in text
    assert "skipped (criteria):" in text


def test_log_records_ai_failure_detail(config, workspace):
    class _Failing:
        def decide(self, *a, **k):
            raise AIError(
                "request failed", status_code=503,
                url="http://host:8001/v1/chat/completions", attempts=2,
                request={"model": "m", "messages": []}, response_text="upstream is down",
            )

    with open_run_log(config) as (log, path), Ledger(config.logging.ledger_db) as ledger:
        proposals, stats = plan(config, _Failing(), ledger, log)

    text = path.read_text(encoding="utf-8")
    assert "FAIL (ai)" in text
    assert "status_code=503" in text
    assert "upstream is down" in text
    assert stats.errors >= 1
    assert proposals == []


def test_already_judged_skip_logged_on_second_run(config, stub_client, workspace):
    # First run proposes; second run should log the hash-dedupe skip.
    with open_run_log(config) as (log, _), Ledger(config.logging.ledger_db) as ledger:
        plan(config, stub_client, ledger, log)
    with open_run_log(config) as (log, path), Ledger(config.logging.ledger_db) as ledger:
        _, stats = plan(config, stub_client, ledger, log)
    text = path.read_text(encoding="utf-8")
    assert "SKIP (already judged):" in text
    assert stats.skipped_seen >= 1
