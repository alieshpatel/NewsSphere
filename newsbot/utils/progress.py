"""
Terminal progress utilities: byte/item progress bars with live speed + ETA,
and an async spinner overlay for long single-await steps.
"""

import sys
import time
import asyncio
from typing import Optional


def _human_bytes(n: float) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _human_time(seconds: float) -> str:
    if seconds is None or seconds != seconds or seconds < 0:  # NaN / invalid guard
        return "--:--"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class ProgressBar:
    """
    A single progress bar with a spinner, fill bar, live speed, and ETA.
    Use show_bytes=True for downloads (reports speed as MB/s),
    or False for item counts (reports speed as items/s).
    """

    SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    BAR_WIDTH = 28

    def __init__(self, total: int, label: str = "", unit: str = "items", show_bytes: bool = False):
        self.total = max(total, 1)
        self.current = 0
        self.label = label
        self.unit = unit
        self.show_bytes = show_bytes
        self.start_time = time.time()
        self._last_update_time = self.start_time
        self._last_current = 0
        self._frame_idx = 0
        self._speed_samples: list[float] = []

    def update(self, amount: int = 1):
        self.set_progress(self.current + amount)

    def set_progress(self, current: int):
        self.current = min(current, self.total)
        self._render()

    def _render(self):
        now = time.time()
        elapsed = now - self.start_time
        dt = now - self._last_update_time

        if dt > 0.05:  # avoid noisy near-zero-interval speed spikes
            inst_speed = (self.current - self._last_current) / dt
            self._speed_samples.append(inst_speed)
            if len(self._speed_samples) > 6:
                self._speed_samples.pop(0)
            self._last_update_time = now
            self._last_current = self.current

        avg_speed = sum(self._speed_samples) / len(self._speed_samples) if self._speed_samples else 0
        pct = self.current / self.total
        filled = int(self.BAR_WIDTH * pct)
        bar = "█" * filled + "░" * (self.BAR_WIDTH - filled)

        remaining = self.total - self.current
        eta = remaining / avg_speed if avg_speed > 0 else float("nan")

        spinner = self.SPINNER_FRAMES[self._frame_idx % len(self.SPINNER_FRAMES)]
        self._frame_idx += 1

        if self.show_bytes:
            speed_str = f"{_human_bytes(avg_speed)}/s"
            progress_str = f"{_human_bytes(self.current)}/{_human_bytes(self.total)}"
        else:
            speed_str = f"{avg_speed:.1f} {self.unit}/s"
            progress_str = f"{self.current}/{self.total} {self.unit}"

        line = (
            f"\r  {spinner} {self.label:<26} |{bar}| {pct*100:5.1f}%  "
            f"{progress_str:<18} {speed_str:<12} "
            f"elapsed {_human_time(elapsed)}  eta {_human_time(eta)}"
        )
        sys.stdout.write(line[:150].ljust(150))
        sys.stdout.flush()

    def finish(self, message: Optional[str] = None):
        elapsed = time.time() - self.start_time
        sys.stdout.write("\r" + " " * 150 + "\r")
        print(f"  ✅ {message or self.label} — done in {_human_time(elapsed)}")
        sys.stdout.flush()


class StepSpinner:
    """
    Async context manager: animates a spinner line for the duration of a
    long single `await` (script gen, voiceover, video assembly, upload...).
    Shows per-step elapsed time AND total pipeline elapsed time.
    Usage:
        async with StepSpinner(4, 13, "Writing video script", pipeline_start):
            script = await script_agent.write_script(selected_story)
    """

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, step_num: int, total_steps: int, label: str, pipeline_start: float):
        self.step_num = step_num
        self.total_steps = total_steps
        self.label = label
        self.pipeline_start = pipeline_start
        self._stop = False
        self._task: Optional[asyncio.Task] = None
        self._start = time.time()

    async def _spin(self):
        idx = 0
        while not self._stop:
            step_elapsed = time.time() - self._start
            total_elapsed = time.time() - self.pipeline_start
            frame = self.FRAMES[idx % len(self.FRAMES)]
            idx += 1
            line = (
                f"\r  {frame} Step {self.step_num}/{self.total_steps}: {self.label:<42} "
                f"| step {_human_time(step_elapsed)} | total {_human_time(total_elapsed)}"
            )
            sys.stdout.write(line.ljust(120))
            sys.stdout.flush()
            await asyncio.sleep(0.1)

    async def __aenter__(self):
        self._task = asyncio.create_task(self._spin())
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._stop = True
        if self._task:
            await self._task
        sys.stdout.write("\r" + " " * 120 + "\r")
        elapsed = time.time() - self._start
        status = "✅" if exc_type is None else "❌"
        print(f"  {status} Step {self.step_num}/{self.total_steps}: {self.label} ({_human_time(elapsed)})")
        sys.stdout.flush()
        return False  # never swallow exceptions