"""CLI-level tests: drive scanfiler.cli.main with a stubbed AI client."""

from __future__ import annotations

from pathlib import Path

import pytest

import scanfiler.cli as cli


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch, stub_client):
    monkeypatch.setattr(cli, "_make_client", lambda cfg: stub_client)


def test_init_writes_and_refuses_overwrite(tmp_path, capsys):
    cfgp = tmp_path / "config.yaml"
    assert cli.main(["-c", str(cfgp), "init"]) == 0
    assert cfgp.is_file()
    # second time refuses
    assert cli.main(["-c", str(cfgp), "init"]) == 1
    assert "not overwriting" in capsys.readouterr().out


def test_plan_then_apply_then_status_then_undo(config_file, workspace, capsys):
    c = str(config_file)
    proposals = str(workspace["root"] / "proposals.jsonl")

    assert cli.main(["-c", c, "plan", "--proposals", proposals]) == 0
    assert Path(proposals).is_file()
    assert "proposed=" in capsys.readouterr().out

    assert cli.main(["-c", c, "apply", "--proposals", proposals]) == 0
    assert "applied=" in capsys.readouterr().out

    assert cli.main(["-c", c, "status"]) == 0
    assert "applied" in capsys.readouterr().out

    assert cli.main(["-c", c, "undo", "--last"]) == 0
    assert "restored" in capsys.readouterr().out


def test_plan_dry_run_writes_nothing(config_file, workspace, capsys):
    proposals = workspace["root"] / "proposals.jsonl"
    cli.main(["-c", str(config_file), "plan", "--proposals", str(proposals), "--dry-run"])
    assert not proposals.exists()
    assert "dry-run" in capsys.readouterr().out


def test_run_review_mode_writes_proposals(config_file, workspace, capsys):
    proposals = workspace["root"] / "proposals.jsonl"
    assert cli.main(["-c", str(config_file), "run", "--proposals", str(proposals)]) == 0
    assert proposals.is_file()


def test_run_auto_mode_applies(config_file, config, workspace, monkeypatch, capsys):
    # Flip mode=auto by rewriting the config file.
    import yaml

    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    data["apply"]["mode"] = "auto"
    config_file.write_text(yaml.safe_dump(data), encoding="utf-8")

    assert cli.main(["-c", str(config_file), "run"]) == 0
    assert "auto-apply" in capsys.readouterr().out
    # something landed in the library
    assert any(config.paths.library_dir.rglob("*.pdf"))


def test_run_skips_when_locked(config_file, config, capsys):
    from scanfiler.lock import file_lock

    lock_path = Path(config.logging.ledger_db).with_suffix(".lock")
    with file_lock(lock_path):  # hold the lock; the run should bail out cleanly
        assert cli.main(["-c", str(config_file), "run"]) == 0
    assert "skip:" in capsys.readouterr().out


def test_status_empty_ledger(config_file, capsys):
    assert cli.main(["-c", str(config_file), "status"]) == 0
    assert "ledger empty" in capsys.readouterr().out


def test_undo_nonexistent_run(config_file, capsys):
    assert cli.main(["-c", str(config_file), "undo", "--run", "deadbeef"]) == 0
    assert "restored 0" in capsys.readouterr().out


def test_run_triggers_self_update_when_enabled(config_file, monkeypatch):
    import scanfiler.self_update as su

    called = {"n": 0}
    monkeypatch.setattr(su, "perform_self_update", lambda cfg, deps: called.__setitem__("n", 1))

    data = __import__("yaml").safe_load(config_file.read_text(encoding="utf-8"))
    data["auto_update"] = {"enabled": True, "verify_signature": False}
    config_file.write_text(__import__("yaml").safe_dump(data), encoding="utf-8")

    cli.main(["-c", str(config_file), "run"])
    assert called["n"] == 1


def test_dry_run_skips_self_update(config_file, monkeypatch):
    import scanfiler.self_update as su

    called = {"n": 0}
    monkeypatch.setattr(su, "perform_self_update", lambda cfg, deps: called.__setitem__("n", 1))

    data = __import__("yaml").safe_load(config_file.read_text(encoding="utf-8"))
    data["auto_update"] = {"enabled": True}
    config_file.write_text(__import__("yaml").safe_dump(data), encoding="utf-8")

    cli.main(["-c", str(config_file), "run", "--dry-run"])
    assert called["n"] == 0


def test_loop_stops_on_keyboard_interrupt(config_file, monkeypatch, capsys):
    def stop(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_run_once", stop)
    assert cli.main(["-c", str(config_file), "loop"]) == 0
    assert "stopped" in capsys.readouterr().out


def test_loop_survives_a_bad_cycle(config_file, monkeypatch, capsys):
    calls = {"n": 0}

    def flaky(cfg, args):
        calls["n"] += 1
        raise RuntimeError("transient cycle failure")

    # First sleep ends the loop so the test doesn't hang; the cycle error must be caught.
    def fake_sleep(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_run_once", flaky)
    monkeypatch.setattr(cli.time, "sleep", fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["-c", str(config_file), "loop"])
    assert calls["n"] == 1
    assert "cycle error" in capsys.readouterr().err
