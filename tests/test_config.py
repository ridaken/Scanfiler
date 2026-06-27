import os

from scanfiler.config import load_config


def test_env_interpolation(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "secret-token")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "paths:\n"
        f"  inbox_dir: {tmp_path / 'in'}\n"
        f"  library_dir: {tmp_path / 'lib'}\n"
        "ai:\n"
        "  api_key: ${AI_API_KEY}\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    assert cfg.ai.api_key == "secret-token"


def test_missing_env_becomes_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("NOPE_VAR", raising=False)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "paths:\n"
        f"  inbox_dir: {tmp_path / 'in'}\n"
        f"  library_dir: {tmp_path / 'lib'}\n"
        "ai:\n"
        "  api_key: ${NOPE_VAR}\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    assert cfg.ai.api_key == ""
