"""Record the sim1 visualization as an MP4.

The page already knows how to draw any instant of a run, so recording it means
driving that page rather than re-implementing the diagram against a plotting
library: a headless Chromium loads ``index.html``, the recorder places the
playhead frame by frame through ``window.SIM1``, and each screenshot is piped
straight into ffmpeg. Nothing is written to disk between the two, so a 90 s clip
costs no temporary space and cannot drift from the interactive viewer.

Playback is stepped by *payload index*, not by wall-clock time. The payload is
denser where the engine refined its tick, so an incident that occupies seconds
of a three-hour run still gets its own seconds of video.
"""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from app.simulations.sim1.models import VideoSpec

# Applied once, after load: drop the parts of the page that are interactive or
# too tall for a frame, size the diagram to whatever is left, and hang the
# watermark off <html> so nothing in the page can paint over it.
_PREPARE = """
([watermark, opacity, timeline]) => {
  const hidden = ['section.controls', 'section.charts', 'footer.foot', '.note', '#scenario-description'];
  if (!timeline) hidden.push('section.timeline');
  for (const selector of hidden) {
    const node = document.querySelector(selector);
    if (node) node.style.display = 'none';
  }
  document.documentElement.style.overflow = 'hidden';

  /* Fit header + diagram + timeline into the frame by sizing the diagram, not
   * by zooming the page: the SVG is width-driven and 2:1, so shrinking the page
   * only widens the layout and the diagram grows right back. Its own
   * preserveAspectRatio then centres it in whatever box it is given.
   *
   * Solving it by iteration rather than arithmetic keeps this independent of
   * the page's paddings, which belong to the stylesheet and change there. */
  const diagram = document.getElementById('diagram');
  // The viewBox leaves room the page uses for its own margins; a frame has
  // none to spare, so crop it to what the diagram actually draws.
  const box = diagram.getBBox();
  const pad = 10;
  diagram.setAttribute('viewBox', `${box.x - pad} ${box.y - pad} ${box.width + 2 * pad} ${box.height + 2 * pad}`);

  let height = window.innerHeight * 0.6;
  for (let pass = 0; pass < 6; pass++) {
    diagram.style.height = `${height}px`;
    const overflow = document.body.scrollHeight - window.innerHeight;
    if (Math.abs(overflow) <= 1) break;
    height = Math.max(240, height - overflow);
  }
  // The ribbons are canvases sized from their layout box, so they have to be
  // told the box just changed.
  window.dispatchEvent(new Event('resize'));

  if (watermark) {
    const mark = document.createElement('div');
    const text = document.createElement('span');
    text.textContent = watermark;
    mark.appendChild(text);
    // Diagonal, over the sparse lower right of the diagram, and deliberately
    // touching the flows there: a mark sitting in clean background can be
    // cropped or painted out, one crossing the drawing cannot.
    Object.assign(mark.style, {
      position: 'fixed',
      left: '80%',
      top: '79%',
      transform: 'translate(-50%, -50%) rotate(-18deg)',
      font: '600 2.4vw/1 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
      letterSpacing: '0.10em',
      whiteSpace: 'nowrap',
      color: `rgba(148, 163, 184, ${opacity})`,
      pointerEvents: 'none',
      zIndex: '9999',
    });
    document.documentElement.appendChild(mark);
  }

  return { diagram: Math.round(height), page: document.body.scrollHeight };
}
"""


def _ffmpeg_command(spec: VideoSpec, destination: Path) -> list[str]:
    metadata = []
    if spec.watermark:
        metadata = [
            "-metadata",
            f"copyright={spec.watermark}",
            "-metadata",
            f"artist={spec.watermark}",
        ]
    return [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "image2pipe",
        "-framerate",
        str(spec.fps),
        "-i",
        "-",
        "-vf",
        f"scale={spec.width}:{spec.height}:flags=lanczos",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(spec.crf),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        *metadata,
        str(destination),
    ]


def frame_indices(points: int, frames: int) -> list[int]:
    """Spread ``frames`` frames evenly over ``points`` payload samples.

    Even in *index* rather than in time on purpose: see the module docstring.
    """
    if points <= 1:
        return [0] * frames
    last = points - 1
    return [round(i * last / (frames - 1)) for i in range(frames)]


def record(
    page: str | Path,
    destination: str | Path,
    spec: VideoSpec = VideoSpec(),
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Render ``page`` (a sim1 ``index.html``) to an MP4 at ``destination``."""
    source = Path(page).resolve()
    if not source.exists():
        raise FileNotFoundError(f"no visualization at {source} — run `app sim1-run` first")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not on PATH; install it (brew install ffmpeg) and retry")

    out_path = Path(destination)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    advance = 1.0 / spec.fps

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            tab = browser.new_page(
                viewport={"width": spec.width, "height": spec.height},
                device_scale_factor=spec.scale,
            )
            tab.goto(source.as_uri())
            try:
                tab.wait_for_function("() => window.SIM1 && window.SIM1.points() > 0", timeout=30_000)
            except PlaywrightTimeout as error:
                raise RuntimeError(
                    f"{source} never exposed a capture hook — it predates `sim1-video`, rebuild it with `app sim1-run`"
                ) from error
            tab.evaluate(_PREPARE, [spec.watermark, spec.watermark_opacity, spec.timeline])
            tab.wait_for_timeout(300)

            points = tab.evaluate("() => window.SIM1.points()")
            indices = frame_indices(points, spec.frames)

            encoder = subprocess.Popen(_ffmpeg_command(spec, out_path), stdin=subprocess.PIPE)
            assert encoder.stdin is not None
            try:
                for number, index in enumerate(indices):
                    tab.evaluate("([i, a]) => window.SIM1.show(i, a)", [index, advance])
                    encoder.stdin.write(tab.screenshot(type="jpeg", quality=spec.quality))
                    if progress is not None:
                        progress(number + 1, spec.frames)
            finally:
                encoder.stdin.close()
                code = encoder.wait()
            if code != 0:
                raise RuntimeError(f"ffmpeg exited {code}")
        finally:
            browser.close()

    return out_path
