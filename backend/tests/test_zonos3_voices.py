"""Encode persona clips to ZONOS3 reference codes once, without a real encoder."""
from pathlib import Path

from announcer import zonos3_engine, zonos3_voices


def fake_encoder(calls):
    def run(cmd, **kwargs):
        calls.append(cmd)
        out = Path(cmd[cmd.index("--audio") + 2])
        out.write_bytes(b"NPY")
        class Done:
            returncode = 0
            stdout = "encoded 180 frames"
            stderr = ""
        return Done()
    return run


def test_encodes_missing_clips_with_the_cli_and_skips_current(tmp_path):
    a, b = tmp_path / "A.mp3", tmp_path / "B.mp3"
    a.write_bytes(b"aaa")
    b.write_bytes(b"bbb")
    codes = tmp_path / "codes"
    calls = []
    enc = zonos3_voices.Encoder(exe="enc.exe", encoder_gguf="zac-encoder.gguf", teacher_gguf="ssl-teacher.gguf",
                                gpu=True, run=fake_encoder(calls))

    result = zonos3_voices.encode_missing({"A": str(a), "B": str(b)}, str(codes), enc)
    assert result == {"A": "encoded", "B": "encoded"}
    assert calls[0][:4] == ["enc.exe", "zac-encoder.gguf", "ssl-teacher.gguf", "--audio"]
    assert calls[0][-1] == "--gpu"
    assert Path(zonos3_engine.codes_path(str(codes), "A", str(a))).exists()

    calls.clear()
    assert zonos3_voices.encode_missing({"A": str(a), "B": str(b)}, str(codes), enc) == {"A": "current", "B": "current"}
    assert calls == []


def test_changed_clip_is_reencoded_and_stale_codes_removed(tmp_path):
    a = tmp_path / "A.mp3"
    a.write_bytes(b"old")
    codes = tmp_path / "codes"
    enc = zonos3_voices.Encoder(exe="enc.exe", encoder_gguf="e", teacher_gguf="t", gpu=False, run=fake_encoder([]))
    zonos3_voices.encode_missing({"A": str(a)}, str(codes), enc)
    old = Path(zonos3_engine.codes_path(str(codes), "A", str(a)))

    a.write_bytes(b"new recording")
    assert zonos3_voices.encode_missing({"A": str(a)}, str(codes), enc) == {"A": "encoded"}
    assert not old.exists()
    assert Path(zonos3_engine.codes_path(str(codes), "A", str(a))).exists()


def test_failed_encode_is_reported_and_leaves_nothing(tmp_path):
    a = tmp_path / "A.mp3"
    a.write_bytes(b"aaa")

    def run(cmd, **kwargs):
        class Failed:
            returncode = 1
            stdout = ""
            stderr = "ffmpeg missing"
        return Failed()

    enc = zonos3_voices.Encoder(exe="enc.exe", encoder_gguf="e", teacher_gguf="t", gpu=True, run=run)
    assert zonos3_voices.encode_missing({"A": str(a)}, str(tmp_path / "codes"), enc) == {"A": "failed"}
    assert list((tmp_path / "codes").glob("*.npy")) == []
