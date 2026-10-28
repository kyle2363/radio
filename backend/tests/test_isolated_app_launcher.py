"""Safety and path validation for the disposable manual QA launcher."""

import pytest

from scripts import run_isolated_app as launcher


def test_qa_workspace_must_be_direct_child_of_project_test_runtime(tmp_path):
    project = tmp_path / "radio"
    runtime = project / ".test-runtime"
    valid = runtime / "qa-a1b2"
    assert launcher.validate_qa_root(project, valid) == valid.resolve()

    for invalid in (project / "data", project / "audio_files", runtime / "nested" / "qa-x"):
        with pytest.raises(ValueError):
            launcher.validate_qa_root(project, invalid)


def test_generated_paths_stay_inside_qa_workspace(tmp_path):
    qa = tmp_path / "qa-123"
    paths = launcher.workspace_paths(qa)

    assert all(launcher.is_within(path, qa) for path in paths.values())
    assert paths["music"] == (qa / "music").resolve()
    assert paths["data"] == (qa / "data").resolve()


def test_launcher_requires_project_backend_virtualenv_python(tmp_path):
    project = tmp_path / "radio"
    good = project / "backend" / "venv" / "Scripts" / "python.exe"
    outside = project / "backend" / "python.exe"

    assert launcher.validate_venv(good, project) == good.resolve()
    with pytest.raises(RuntimeError):
        launcher.validate_venv(outside, project)


def test_environment_config_uses_only_disposable_locations(tmp_path):
    qa = tmp_path / "qa-env"
    qa.mkdir()
    env = {"HTTPS_PROXY": "must-be-removed", "RADIO_PASSWORD": "old"}
    paths, secret = launcher.configure_environment(qa, 18765, env)

    assert env["RADIO_DATA_DIR"] == str(paths["data"])
    assert env["RADIO_MUSIC_DIR"] == str(paths["music"])
    assert env["RADIO_METADATA_DIR"] == str(paths["metadata"])
    for key in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA"):
        assert launcher.is_within(env[key], qa)
    assert env["RADIO_TTS_ENABLED"] == "false"
    assert env["RADIO_PROC_MGR_ENABLED"] == "false"
    assert env["RADIO_PASSWORD"] == launcher.QA_PASSWORD
    assert env["RADIO_AUTH_SECRET"] == secret and secret
    assert "HTTPS_PROXY" not in env


def test_network_guard_allowlist_is_loopback_and_exact_port_only():
    assert launcher.is_allowed_outbound_address(("127.0.0.1", 18765), 18765)
    assert launcher.is_allowed_outbound_address(("::1", 18765), 18765)
    assert launcher.is_allowed_outbound_address(("localhost", 18765), 18765)
    assert not launcher.is_allowed_outbound_address(("127.0.0.1", 8081), 18765)
    assert not launcher.is_allowed_outbound_address(("192.168.1.20", 18765), 18765)
    assert not launcher.is_allowed_outbound_address(("example.com", 18765), 18765)
def test_qa_storage_policy_rejects_original_data_and_outside_paths(tmp_path):
    import importlib.util
    from pathlib import Path
    script = Path(__file__).parents[2] / 'scripts' / 'run_isolated_app.py'
    spec = importlib.util.spec_from_file_location('qa_storage', script)
    qa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qa)
    root = tmp_path / 'qa'
    root.mkdir()
    assert qa.is_allowed_write(root / 'data' / 'radio.db', root)
    assert not qa.is_allowed_write(tmp_path / 'audio_files' / 'song.mp3', root)
    assert not qa.is_allowed_write(root / '..' / 'radio.db', root)
