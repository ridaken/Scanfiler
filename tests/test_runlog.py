import time

from scanfiler.config import Config
from scanfiler.runlog import open_run_log


def _cfg(tmp_path, enabled=True):
    return Config.model_validate({
        "paths": {"inbox_dir": str(tmp_path / "in"), "library_dir": str(tmp_path / "lib")},
        "logging": {"enabled": enabled, "log_dir": str(tmp_path / "logs")},
    })


def test_creates_timestamped_file(tmp_path):
    cfg = _cfg(tmp_path)
    with open_run_log(cfg) as (log, path):
        assert path is not None
        log.info("hello-line")
    assert path.exists()                       # handler flushed + closed on exit
    assert path.name.startswith("scanfiler-") and path.suffix == ".log"
    assert "hello-line" in path.read_text(encoding="utf-8")


def test_disabled_yields_no_file(tmp_path):
    cfg = _cfg(tmp_path, enabled=False)
    with open_run_log(cfg) as (log, path):
        assert path is None
        log.info("ignored")                    # no-op, must not raise
    assert not (tmp_path / "logs").exists()


def test_separate_runs_get_distinct_files(tmp_path):
    cfg = _cfg(tmp_path)
    paths = []
    for _ in range(2):
        with open_run_log(cfg) as (log, path):
            log.info("x")
            paths.append(path)
        time.sleep(0.003)                       # ensure the ms-precision timestamp differs
    assert paths[0] != paths[1]
