import importlib.util
from pathlib import Path

import config


def test_isolated_instance_paths_and_optional_services_are_configurable(monkeypatch):
    root = Path(config.DB_PATH).parent
    monkeypatch.setenv("RADIO_DATA_DIR", str(root / "runtime"))
    monkeypatch.setenv("RADIO_MUSIC_DIR", str(root / "music"))
    monkeypatch.setenv("RADIO_METADATA_DIR", str(root / "metadata"))
    monkeypatch.setenv("RADIO_TTS_ENABLED", "false")
    monkeypatch.setenv("RADIO_PROC_MGR_ENABLED", "false")
    source = Path(__file__).parents[1] / "config.py"
    spec = importlib.util.spec_from_file_location("_isolated_config_paths", source)
    isolated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated)
    assert Path(isolated.DATA_DIR) == root / "runtime"
    assert Path(isolated.MUSIC_DIR) == root / "music"
    assert Path(isolated.ACESTEP_JSON_DIR) == root / "metadata"
    assert not isolated.TTS_ENABLED
    assert not isolated.PROC_MGR_ENABLED
    for name in ("DB_PATH", "CHROMA_PATH", "ART_DIR", "TTS_CACHE_DIR", "LOG_PATH", "TALK_DEBUG_LOG_PATH"):
        assert Path(getattr(isolated, name)).is_relative_to(root / "runtime"), name
