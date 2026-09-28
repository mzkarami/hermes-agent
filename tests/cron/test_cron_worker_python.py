"""External cron workers must inherit the selected dependency interpreter."""

import json
import sys
from pathlib import Path

from cron.scheduler_worker_env import cron_worker_python
from pm.environments import install_state_dir


def test_cron_worker_python_uses_each_profiles_committed_environment(tmp_path, monkeypatch):
    repo_root = tmp_path / "checkout"
    repo_root.mkdir()
    selected = []
    for name in ("a", "b"):
        home = tmp_path / name
        monkeypatch.setenv("HERMES_HOME", str(home))
        state = install_state_dir(repo_root)
        environment = state / "environments" / name / "venv"
        python = environment / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.touch()
        (environment / "pyvenv.cfg").write_text("version = 3.14.7\n")
        state.mkdir(parents=True, exist_ok=True)
        (state / "facts.json").write_text(json.dumps({
            "packages": {"venv": {"environment": str(environment)}}
        }))
        selected.append(str(python))
        assert cron_worker_python(repo_root) == str(python)

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "a"))
    assert cron_worker_python(repo_root) == selected[0]
    assert selected[0] != selected[1]


def test_cron_worker_python_keeps_legacy_interpreter_and_rejects_missing_selection(
    tmp_path, monkeypatch,
):
    import pytest

    repo_root = tmp_path / "checkout"
    repo_root.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "legacy"))
    assert cron_worker_python(repo_root) == sys.executable

    state = install_state_dir(repo_root)
    environment = state / "environments" / "broken" / "venv"
    environment.mkdir(parents=True)
    (environment / "pyvenv.cfg").write_text("version = 3.14.7\n")
    state.mkdir(parents=True, exist_ok=True)
    (state / "facts.json").write_text(json.dumps({
        "packages": {"venv": {"environment": str(environment)}}
    }))
    with pytest.raises(RuntimeError, match="cron worker dependency interpreter is missing"):
        cron_worker_python(repo_root)
