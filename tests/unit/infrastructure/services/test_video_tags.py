'''
File:   test_video_tags.py
Brief:  Unit tests for ffprobe container-tag extraction.
Author: Mistress-Lukutar
Date:   2026-11-03
Version: v2.1.0

Tests ``probe_video_tags`` with the ffprobe seam stubbed out — tests
never touch the network or a real binary.
'''
import json
import subprocess
from pathlib import Path

from app.infrastructure.services import ffmpeg as ffmpeg_module
from app.infrastructure.services.ffmpeg import probe_video_tags


def _stub_ffprobe(monkeypatch, stdout: str = "", returncode: int = 0):
    """Stub the ffprobe binary resolution and process invocation."""
    monkeypatch.setattr(
        ffmpeg_module, "_get_ffmpeg_binary", lambda name: Path("ffprobe")
    )

    def fake_run(binary, args, timeout=30):
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    monkeypatch.setattr(ffmpeg_module, "_run_tool", fake_run)


class TestProbeVideoTags:
    def test_parses_format_tags(self, monkeypatch):
        payload = json.dumps({
            "format": {
                "tags": {
                    "creation_time": "2026-06-01T10:00:00.000000Z",
                    "com.apple.quicktime.make": "Apple",
                    "com.apple.quicktime.model": "iPhone 15 Pro",
                }
            }
        })
        _stub_ffprobe(monkeypatch, stdout=payload)
        tags = probe_video_tags(Path("video.mp4"))
        assert tags["com.apple.quicktime.make"] == "Apple"
        assert tags["com.apple.quicktime.model"] == "iPhone 15 Pro"
        assert tags["creation_time"].startswith("2026-06-01")

    def test_empty_and_blank_values_filtered(self, monkeypatch):
        payload = json.dumps({
            "format": {"tags": {"encoder": "Lavf", "comment": "", "x": None}}
        })
        _stub_ffprobe(monkeypatch, stdout=payload)
        assert probe_video_tags(Path("video.mp4")) == {"encoder": "Lavf"}

    def test_no_tags_returns_empty(self, monkeypatch):
        _stub_ffprobe(monkeypatch, stdout=json.dumps({"format": {}}))
        assert probe_video_tags(Path("video.mp4")) == {}

    def test_missing_binary_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            ffmpeg_module, "_get_ffmpeg_binary", lambda name: None
        )
        assert probe_video_tags(Path("video.mp4")) == {}

    def test_failure_returncode_returns_empty(self, monkeypatch):
        _stub_ffprobe(monkeypatch, stdout="{}", returncode=1)
        assert probe_video_tags(Path("video.mp4")) == {}

    def test_non_json_output_returns_empty(self, monkeypatch):
        _stub_ffprobe(monkeypatch, stdout="not json at all")
        assert probe_video_tags(Path("video.mp4")) == {}
