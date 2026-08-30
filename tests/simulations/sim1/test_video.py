"""The recorder, and the page hook it drives.

The end-to-end test is a smoke test on purpose: it proves the pieces still fit
together — the page exposes ``window.SIM1``, Chromium renders it, ffmpeg accepts
the frames — rather than that any particular pixel is right.
"""

import shutil

import pytest

from app.simulations.sim1.engine import run_scenario
from app.simulations.sim1.report import write_artifacts
from app.simulations.sim1.scenarios import get_scenario
from app.simulations.sim1.video import VideoSpec, _ffmpeg_command, frame_indices, record


def test_frame_indices_span_the_payload() -> None:
    indices = frame_indices(points=1000, frames=60)
    assert len(indices) == 60
    assert indices[0] == 0
    assert indices[-1] == 999
    assert indices == sorted(indices)


def test_frame_indices_survive_a_payload_shorter_than_the_clip() -> None:
    # More frames than points: every frame still names a real sample.
    indices = frame_indices(points=3, frames=10)
    assert set(indices) <= {0, 1, 2}
    assert indices[0] == 0 and indices[-1] == 2

    assert frame_indices(points=1, frames=4) == [0, 0, 0, 0]


def test_spec_frames_follow_length_and_rate() -> None:
    assert VideoSpec(seconds=45.0, fps=30).frames == 1350
    assert VideoSpec(seconds=0.0, fps=30).frames == 2  # a clip is never a single frame


def test_the_watermark_reaches_the_file_metadata(tmp_path) -> None:
    spec = VideoSpec(watermark="© 2026 A Name")
    command = _ffmpeg_command(spec, tmp_path / "clip.mp4")
    assert "copyright=© 2026 A Name" in command
    assert "artist=© 2026 A Name" in command
    assert "copyright=" not in " ".join(_ffmpeg_command(VideoSpec(), tmp_path / "clip.mp4"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_record_writes_a_playable_clip(tmp_path) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        with playwright.sync_playwright() as instance:
            instance.chromium.launch().close()
    except Exception as error:  # pragma: no cover - depends on the machine
        pytest.skip(f"no Chromium for Playwright: {error}")

    result = run_scenario("grid_outage_gen_ok", duration_s=600.0, dt=10.0)
    artifacts = write_artifacts(
        result, get_scenario("grid_outage_gen_ok"), tmp_path, analysis=False, csv=False, viz_points=120
    )
    assert artifacts.viz is not None

    seen: list[int] = []
    clip = record(
        artifacts.viz,
        tmp_path / "clip.mp4",
        VideoSpec(seconds=0.5, fps=10, width=640, height=360, scale=1, watermark="© test"),
        progress=lambda number, _total: seen.append(number),
    )

    assert clip.exists() and clip.stat().st_size > 1024
    assert seen == [1, 2, 3, 4, 5]
