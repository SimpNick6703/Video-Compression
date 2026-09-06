"""
Progress tracking and rich TUI display for encoding operations.

Provides:
  - ProgressTracker: Thread-safe state container for dual-process monitoring
  - monitor_process: Reads FFmpeg stderr and updates tracker
  - Rich-based TUI: Encoder detection table, dual/single progress panels,
    result summary, and exit countdown
"""

import sys
import time
import threading
import logging
from collections import deque
from typing import List, Tuple, Optional

from rich.console import Console, RenderableType
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.progress_bar import ProgressBar
from rich.rule import Rule

from videocompress.core import get_display_name

log = logging.getLogger(__name__)

console = Console(safe_box=True)

PROGRESS_KEYS = frozenset({"frame", "fps", "bitrate", "total_size", "out_time_us", "out_time", "speed", "progress"})


def _detect_spinner_frames() -> List[str]:
    """Detect if terminal encoding supports braille spinner or fallback to ASCII."""
    encoding = getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
    try:
        "⠋".encode(encoding)
        return list("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
    except (UnicodeEncodeError, LookupError):
        return list(r"|/-\ ")


SPINNER_FRAMES = _detect_spinner_frames()

# Overhead of fixed columns (label + FPS + Bitrate + Speed + ETA + chrome + percentage)
_FIXED_OVERHEAD = 74
_MIN_BAR_WIDTH = 12


def get_bar_width() -> int:
    """Compute progress bar width dynamically from terminal width."""
    return max(_MIN_BAR_WIDTH, console.width - _FIXED_OVERHEAD)


def format_eta(seconds: float) -> str:
    """Format seconds into a compact ETA string.

    Args:
        seconds: Remaining time in seconds.

    Returns:
        Human-readable ETA like "0m 35s" or "1h 02m".
    """
    seconds = max(0.0, seconds)
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60):02d}s"
    return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60):02d}m"


# --- Thread-safe Progress Tracking ---

class ProgressTracker:
    """Thread-safe progress state for dual-process encoding.

    Stores per-segment timing, FPS, and speed. Accessed from both
    the monitor threads and the main display thread.
    """

    def __init__(self, dur_a: float, dur_b: float) -> None:
        self.lock = threading.Lock()
        self.dur_a = dur_a
        self.dur_b = dur_b
        self.time_a = 0.0
        self.time_b = 0.0
        self.fps_a = 0.0
        self.fps_b = 0.0
        self.spd_a = 0.001
        self.spd_b = 0.001
        self.bitrate_a = 0
        self.bitrate_b = 0

    def update_field(self, is_segment_a: bool, key: str, val: str) -> None:
        """Update a specific telemetry field thread-safely."""
        with self.lock:
            if key == "out_time_us":
                try:
                    t = float(val) / 1_000_000.0
                    if is_segment_a:
                        self.time_a = t
                    else:
                        self.time_b = t
                except ValueError:
                    pass
            elif key == "fps":
                try:
                    fps = float(val) if val != "N/A" else 0.0
                    if is_segment_a:
                        self.fps_a = fps
                    else:
                        self.fps_b = fps
                except ValueError:
                    pass
            elif key == "speed":
                try:
                    spd_s = val.rstrip("x").strip()
                    spd = float(spd_s) if spd_s != "N/A" else 0.001
                    if is_segment_a:
                        self.spd_a = max(spd, 0.001)
                    else:
                        self.spd_b = max(spd, 0.001)
                except ValueError:
                    pass
            elif key == "bitrate":
                try:
                    br_s = val.rstrip("kbits/s").strip()
                    br = int(float(br_s)) if br_s != "N/A" else 0
                    if is_segment_a:
                        self.bitrate_a = br
                    else:
                        self.bitrate_b = br
                except ValueError:
                    pass

    def get_worker_stats(self) -> Tuple[float, float, float, float, float, float, int, int, float, float, float, float]:
        """Return per-worker stats and timestamps for the dual display.

        Returns:
            Tuple of (prog_a, prog_b, fps_a, fps_b, spd_a, spd_b,
            bitrate_a, bitrate_b, eta_a, eta_b, time_a, time_b).
        """
        with self.lock:
            prog_a = min(100.0, (self.time_a / self.dur_a) * 100) if self.dur_a > 0 else 100.0
            prog_b = min(100.0, (self.time_b / self.dur_b) * 100) if self.dur_b > 0 else 100.0
            eta_a = max(0.0, (self.dur_a - self.time_a) / self.spd_a) if self.spd_a > 0 else 0.0
            eta_b = max(0.0, (self.dur_b - self.time_b) / self.spd_b) if self.spd_b > 0 else 0.0
            return (prog_a, prog_b, self.fps_a, self.fps_b, self.spd_a, self.spd_b,
                    self.bitrate_a, self.bitrate_b, eta_a, eta_b, self.time_a, self.time_b)


class SingleProgressState:
    """Thread-safe progress state for single-process encoding."""

    def __init__(self, duration: float) -> None:
        self.lock = threading.Lock()
        self.duration = duration
        self.current_time = 0.0
        self.fps = 0.0
        self.speed = 0.001
        self.bitrate = 0

    def update_field(self, key: str, val: str) -> None:
        """Update a specific telemetry field thread-safely."""
        with self.lock:
            if key == "out_time_us":
                try:
                    self.current_time = float(val) / 1_000_000.0
                except ValueError:
                    pass
            elif key == "fps":
                try:
                    self.fps = float(val) if val != "N/A" else 0.0
                except ValueError:
                    pass
            elif key == "speed":
                try:
                    spd_s = val.rstrip("x").strip()
                    self.speed = max(float(spd_s) if spd_s != "N/A" else 0.001, 0.001)
                except ValueError:
                    pass
            elif key == "bitrate":
                try:
                    br_s = val.rstrip("kbits/s").strip()
                    self.bitrate = int(float(br_s)) if br_s != "N/A" else 0
                except ValueError:
                    pass

    def get_stats(self) -> Tuple[float, float, float, int, float]:
        """Return (progress%, fps, speed, bitrate, eta_seconds)."""
        with self.lock:
            prog = min(100.0, (self.current_time / self.duration) * 100) if self.duration > 0 else 100.0
            eta = max(0.0, (self.duration - self.current_time) / self.speed) if self.speed > 0 else 0.0
            return prog, self.fps, self.speed, self.bitrate, eta


# --- FFmpeg Telemetry & Error Ring Buffer Monitoring ---

def monitor_process(
    process: "subprocess.Popen[str]",
    tracker: ProgressTracker,
    is_segment_a: bool,
    error_buffer: Optional[deque[str]] = None,
) -> None:
    """Read FFmpeg stderr line-by-line, updating telemetry and error buffer.

    Designed to run in a daemon thread. Consumes from stderr until EOF.

    Args:
        process: A Popen object with stderr=subprocess.PIPE, text=True.
        tracker: Shared ProgressTracker instance.
        is_segment_a: True for segment A, False for segment B.
        error_buffer: Optional bounded ring buffer for error diagnostics.
    """
    assert process.stderr is not None
    for raw_line in iter(process.stderr.readline, ''):
        line = raw_line.strip()
        if not line:
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if key in PROGRESS_KEYS:
                tracker.update_field(is_segment_a, key, val)
            else:
                if error_buffer is not None:
                    error_buffer.append(line)
        else:
            if error_buffer is not None:
                error_buffer.append(line)


def monitor_single_process(
    process: "subprocess.Popen[str]",
    state: SingleProgressState,
    error_buffer: Optional[deque[str]] = None,
) -> None:
    """Read FFmpeg stderr for a single process and update state."""
    assert process.stderr is not None
    for raw_line in iter(process.stderr.readline, ''):
        line = raw_line.strip()
        if not line:
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if key in PROGRESS_KEYS:
                state.update_field(key, val)
            else:
                if error_buffer is not None:
                    error_buffer.append(line)
        else:
            if error_buffer is not None:
                error_buffer.append(line)


# --- Rich TUI Display Builders ---

def build_worker_progress_cell(prog: float, bar_w: int) -> RenderableType:
    """Build a worker row progress cell: bar + right-aligned percentage."""
    if prog >= 100:
        return Text("Done", style="green")

    row = Table.grid(padding=(0, 1))
    row.add_column(width=bar_w)
    row.add_column(justify="right", width=7)
    row.add_row(
        ProgressBar(total=100, completed=prog, width=bar_w, complete_style="cyan", finished_style="green"),
        Text(f"{prog:.2f}%", style="white"),
    )
    return row


def build_total_progress_cell(prog: float, frame_idx: int, bar_w: int) -> RenderableType:
    """Build the Total row: spinner + bar + right-aligned percentage."""
    spinner_char = SPINNER_FRAMES[frame_idx % len(SPINNER_FRAMES)]
    inner_bar_w = max(_MIN_BAR_WIDTH, bar_w - 3)

    if prog >= 100:
        s_style, b_style = "green", "green"
        pct = Text("100.00%", style="bold green")
    else:
        s_style, b_style = "cyan", "cyan"
        pct = Text(f"{prog:.2f}%", style="bold white")

    row = Table.grid(padding=(0, 1))
    row.add_column(width=2)
    row.add_column(width=inner_bar_w)
    row.add_column(justify="right", width=7)
    row.add_row(
        Text(spinner_char, style=s_style),
        ProgressBar(total=100, completed=min(prog, 100), width=inner_bar_w, complete_style=b_style, finished_style="green"),
        pct,
    )
    return row


def build_single_progress_cell(prog: float, frame_idx: int, bar_w: int) -> RenderableType:
    """Build a single-process progress cell: spinner + bar + percentage."""
    return build_total_progress_cell(prog, frame_idx, bar_w)


def build_dual_display(
    prog_a: float, prog_b: float,
    fps_a: float, fps_b: float,
    speed_a: float, speed_b: float,
    bitrate_a: int, bitrate_b: int,
    eta_a: float, eta_b: float,
    pass_label: str,
    frame_idx: int,
    total_prog: Optional[float] = None,
) -> Panel:
    """Build the dual-worker progress panel."""
    bar_w = get_bar_width()

    table = Table(
        show_header=True,
        header_style="bold dim",
        border_style="dim",
        pad_edge=True,
    )
    table.add_column("", style="bold", width=10)
    table.add_column("Progress", min_width=bar_w + 10, no_wrap=True)
    table.add_column("FPS", justify="right", style="yellow", width=8)
    table.add_column("Bitrate", justify="right", style="#d8a0ff", width=10)
    table.add_column("Speed", justify="right", style="green", width=8)
    table.add_column("ETA", justify="right", style="cyan", width=8)

    for label, prog, fps, br, spd, eta in [
        ("Worker 1", prog_a, fps_a, bitrate_a, speed_a, eta_a),
        ("Worker 2", prog_b, fps_b, bitrate_b, speed_b, eta_b),
    ]:
        table.add_row(
            f"[cyan]{label}[/]",
            build_worker_progress_cell(prog, bar_w),
            f"{fps:.1f}",
            f"{br}k" if br > 0 else "[dim]-[/]",
            f"{spd:.2f}x",
            format_eta(eta),
        )

    if total_prog is None:
        total_prog = (prog_a + prog_b) / 2
    total_fps = fps_a + fps_b
    active_spd_a = speed_a if prog_a < 100.0 else 0.0
    active_spd_b = speed_b if prog_b < 100.0 else 0.0
    combined_speed = (active_spd_a + active_spd_b) if (active_spd_a + active_spd_b) > 0 else (speed_a + speed_b) / 2
    max_eta = max(eta_a if prog_a < 100 else 0.0, eta_b if prog_b < 100 else 0.0)

    table.add_section()
    table.add_row(
        "[bold white]Total[/]",
        build_total_progress_cell(total_prog, frame_idx, bar_w),
        f"[bold]{total_fps:.1f}[/]",
        "",
        f"[bold]{combined_speed:.2f}x[/]",
        f"[bold]{format_eta(max_eta)}[/]",
    )

    return Panel(
        table,
        title=f"[bold]{pass_label}",
        title_align="left",
        border_style="cyan",
        padding=(0, 0),
    )


def build_single_display(
    prog: float,
    fps: float,
    speed: float,
    bitrate: int,
    eta: float,
    pass_label: str,
    frame_idx: int,
) -> Panel:
    """Build a single-process progress panel."""
    bar_w = get_bar_width()

    table = Table(
        show_header=True,
        header_style="bold dim",
        border_style="dim",
        pad_edge=True,
    )
    table.add_column("Progress", min_width=bar_w + 10, no_wrap=True)
    table.add_column("FPS", justify="right", style="yellow", width=8)
    table.add_column("Bitrate", justify="right", style="#d8a0ff", width=10)
    table.add_column("Speed", justify="right", style="green", width=8)
    table.add_column("ETA", justify="right", style="cyan", width=8)

    table.add_row(
        build_single_progress_cell(prog, frame_idx, bar_w),
        f"{fps:.1f}",
        f"{bitrate}k" if bitrate > 0 else "[dim]-[/]",
        f"{speed:.2f}x",
        format_eta(eta),
    )

    return Panel(
        table,
        title=f"[bold]{pass_label}",
        title_align="left",
        border_style="cyan",
        padding=(0, 0),
    )


# --- High-level Display Functions ---

def show_encoder_detection(results: List[Tuple[str, str]]) -> None:
    """Print the encoder detection table.

    Args:
        results: List of (encoder_name, status) from select_best_encoder.
    """
    console.print(Rule("[bold cyan]Encoder Detection[/]", style="dim"))
    console.print()

    table = Table(
        border_style="dim",
        show_header=True,
        header_style="bold dim",
        pad_edge=True,
    )
    table.add_column("Encoder", style="white", min_width=20)
    table.add_column("Status", justify="center", min_width=14)

    for name, status in results:
        display = get_display_name(name)
        if status == "selected":
            table.add_row(f"[bold cyan]{display}[/]", "[bold green]Selected[/]")
        elif status == "available":
            table.add_row(display, "[green]Available[/]")
        elif status == "unavailable":
            table.add_row(display, "[#ff6b6b]Unavailable[/]")
        elif status == "skipped":
            table.add_row(f"[dim]{display}[/]", "[dim]Skipped[/]")

    console.print(table)
    console.print()


def run_dual_progress(
    proc_a: "subprocess.Popen[str]",
    proc_b: "subprocess.Popen[str]",
    dur_a: float,
    dur_b: float,
    bitrate_a: int,
    bitrate_b: int,
    pass_label: str,
) -> bool:
    """Monitor two parallel FFmpeg processes with a live dual-worker display.

    Args:
        proc_a: First FFmpeg subprocess (stderr=PIPE, text=True).
        proc_b: Second FFmpeg subprocess (stderr=PIPE, text=True).
        dur_a: Duration of segment A in seconds.
        dur_b: Duration of segment B in seconds.
        bitrate_a: Target bitrate for segment A in kbps.
        bitrate_b: Target bitrate for segment B in kbps.
        pass_label: Display label (e.g., "Pass 1/2 -- Analysis").

    Returns:
        True if both processes exited with code 0.
    """
    import subprocess as _sp  # for type only, already imported at module scope indirectly

    tracker = ProgressTracker(dur_a, dur_b)
    tracker.bitrate_a = bitrate_a
    tracker.bitrate_b = bitrate_b
    error_buffer: deque[str] = deque(maxlen=25)

    t1 = threading.Thread(target=monitor_process, args=(proc_a, tracker, True, error_buffer), daemon=True)
    t2 = threading.Thread(target=monitor_process, args=(proc_b, tracker, False, error_buffer), daemon=True)
    t1.start()
    t2.start()

    frame_idx = 0
    initial = build_dual_display(0, 0, 0, 0, 0, 0, bitrate_a, bitrate_b, dur_a, dur_b, pass_label, 0, 0.0)

    failed = False
    with Live(initial, refresh_per_second=8, console=console) as live:
        while proc_a.poll() is None or proc_b.poll() is None:
            ret_a = proc_a.poll()
            ret_b = proc_b.poll()
            if (ret_a not in (None, 0)) or (ret_b not in (None, 0)):
                failed = True
                for p in (proc_a, proc_b):
                    if p.poll() is None:
                        try:
                            p.kill()
                            p.wait(timeout=1.0)
                        except Exception:
                            pass
                break

            frame_idx += 1
            stats = tracker.get_worker_stats()
            prog_a, prog_b, fps_a, fps_b, spd_a, spd_b, br_a, br_b, eta_a, eta_b, t_a, t_b = stats
            total_dur = dur_a + dur_b
            total_prog = min(100.0, ((t_a + t_b) / total_dur) * 100.0) if total_dur > 0 else 100.0

            live.update(build_dual_display(
                prog_a, prog_b, fps_a, fps_b, spd_a, spd_b,
                br_a, br_b, eta_a, eta_b, pass_label, frame_idx, total_prog,
            ))
            time.sleep(0.1)

        if not failed:
            frame_idx += 1
            stats = tracker.get_worker_stats()
            prog_a, prog_b, fps_a, fps_b, spd_a, spd_b, br_a, br_b, eta_a, eta_b, t_a, t_b = stats
            live.update(build_dual_display(
                min(prog_a, 100), min(prog_b, 100),
                fps_a, fps_b, spd_a, spd_b,
                br_a, br_b, 0, 0, pass_label, frame_idx, 100.0,
            ))

    t1.join(timeout=2)
    t2.join(timeout=2)

    if failed or proc_a.returncode != 0 or proc_b.returncode != 0:
        err_msg = "\n".join(list(error_buffer)[-10:])
        if err_msg:
            console.print(f"[bold red]FFmpeg Error Details:[/]\n[dim]{err_msg}[/]\n")
        return False

    return True


def run_single_progress(
    process: "subprocess.Popen[str]",
    duration: float,
    bitrate_k: int,
    pass_label: str = "Encoding",
) -> bool:
    """Monitor a single FFmpeg process with a live progress display.

    Args:
        process: FFmpeg subprocess (stderr=PIPE, text=True).
        duration: Expected duration in seconds.
        bitrate_k: Target bitrate in kbps.
        pass_label: Display label.

    Returns:
        True if the process exited with code 0.
    """
    state = SingleProgressState(duration)
    state.bitrate = bitrate_k
    error_buffer: deque[str] = deque(maxlen=25)

    t = threading.Thread(target=monitor_single_process, args=(process, state, error_buffer), daemon=True)
    t.start()

    frame_idx = 0
    initial = build_single_display(0, 0, 0, bitrate_k, duration, pass_label, 0)

    failed = False
    with Live(initial, refresh_per_second=8, console=console) as live:
        while process.poll() is None:
            frame_idx += 1
            prog, fps, speed, br, eta = state.get_stats()
            live.update(build_single_display(prog, fps, speed, br, eta, pass_label, frame_idx))
            time.sleep(0.1)

        if process.returncode not in (None, 0):
            failed = True
        else:
            frame_idx += 1
            prog, fps, speed, br, eta = state.get_stats()
            live.update(build_single_display(min(prog, 100), fps, speed, br, 0, pass_label, frame_idx))

    t.join(timeout=2)

    if failed or process.returncode != 0:
        err_msg = "\n".join(list(error_buffer)[-10:])
        if err_msg:
            console.print(f"[bold red]FFmpeg Error Details:[/]\n[dim]{err_msg}[/]\n")
        return False

    return True


def show_result_panel(
    original_bytes: int,
    final_bytes: int,
    bitrate_k: int,
    elapsed_sec: float,
    encoder: str,
    mode: str,
    split_info: Optional[str] = None,
    quality_info: Optional[str] = None,
) -> None:
    """Print the final encode result panel.

    Args:
        original_bytes: Original file size in bytes.
        final_bytes: Final file size in bytes.
        bitrate_k: Final bitrate in kbps.
        elapsed_sec: Total encoding time in seconds.
        encoder: FFmpeg encoder name used.
        mode: Encoding mode description (e.g., "2-pass split").
        split_info: Optional split durations string (e.g., "65.0s + 55.0s").
        quality_info: Optional quality/resolution string (e.g., "1080p60 -> 720p60 (scaled)").
    """
    from videocompress.core import format_size, MB_TO_BYTES

    console.print(Rule("[bold green]Result[/]", style="green"))
    console.print()

    orig_mb = original_bytes / MB_TO_BYTES
    final_mb = final_bytes / MB_TO_BYTES
    reduction = ((original_bytes - final_bytes) / original_bytes) * 100 if original_bytes > 0 else 0

    lines = [
        "[bold green]Encode Complete[/]\n",
        f"  [dim]Original:[/]  [white]{orig_mb:.2f} MB[/]",
        f"  [dim]Final:[/]     [white]{final_mb:.2f} MB[/]  [green](-{reduction:.1f}%)[/]",
        f"  [dim]Bitrate:[/]   [#d8a0ff]{bitrate_k}k[/]",
    ]
    if quality_info:
        lines.append(f"  [dim]Quality:[/]   [white]{quality_info}[/]")
    lines.extend([
        f"  [dim]Time:[/]      [white]{elapsed_sec:.1f}s[/]",
        f"  [dim]Encoder:[/]   [cyan]{get_display_name(encoder)}[/]  [dim]({mode})[/]",
    ])
    if split_info:
        lines.append(f"  [dim]Split:[/]     [white]{split_info}[/]")

    result = Panel.fit(
        "\n".join(lines),
        border_style="green",
        title="[bold]Summary",
        title_align="left",
        padding=(0, 2),
    )
    console.print(result)
    console.print()


def show_exit_countdown(seconds: int = 3) -> None:
    """Display a real-time exit countdown.

    Args:
        seconds: Number of seconds to count down from.
    """
    for remaining in range(seconds, 0, -1):
        suffix = "second" if remaining == 1 else "seconds"
        console.print(f"[dim]Exiting in {remaining} {suffix}...[/]", end="\r")
        time.sleep(1.0)
    console.print("[dim]Exiting...                    [/]")
