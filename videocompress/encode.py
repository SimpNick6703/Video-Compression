"""
Encoding orchestration and encoder-specific execution pipelines.

Provides:
  - `compress_video`: Main orchestrator for video compression
  - `_encode_nvenc_2pass`: Parallel 2-pass split pipeline (NVENC / Windows AMF)
  - `_encode_hw_split`: Parallel split single-pass pipeline (VAAPI, QSV, VideoToolbox)
  - `_encode_cpu_single`: Serial single-pass pipeline (CPU fallback: libx265, libx264)
"""

import os
import sys
import time
import tempfile
import subprocess
import logging
from pathlib import Path
from typing import Tuple, Optional, Sequence

from rich.rule import Rule

from videocompress.core import (
    MB_TO_BYTES,
    get_resource_path,
    get_clean_env,
    get_file_size,
    clean_log_file,
    get_video_info,
    get_smart_split_point,
    get_optimal_settings,
    select_best_encoder,
    build_single_pass_cmd,
    build_cpu_pass_cmd,
    calculate_video_bitrate,
    calculate_split_bitrates,
)
from videocompress.progress import (
    console,
    show_encoder_detection,
    run_dual_progress,
    run_single_progress,
    show_result_panel,
    show_exit_countdown,
)

log = logging.getLogger(__name__)


def _encode_hw_split(
    ffmpeg_exe: str,
    input_path: str,
    output_path: str,
    active_encoder: str,
    codec_type: str,
    target_size_mb: float,
    split_time: float,
    durs: Tuple[float, float],
    audio_kbps: int,
    fps: float,
    src_h: int,
    opt_h: int,
    opt_fps: float,
) -> Tuple[bool, str]:
    """Execute parallel split single-pass encoding for hardware encoders.

    Args:
        ffmpeg_exe: Path to the ffmpeg executable.
        input_path: Source video path.
        output_path: Destination file path.
        active_encoder: Active hardware encoder name (e.g., 'hevc_nvenc', 'hevc_amf').
        codec_type: "hevc" or "h264".
        target_size_mb: Total target size in megabytes.
        split_time: Timestamp in seconds marking the segment boundary.
        durs: Tuple of (first_segment_duration, second_segment_duration) in seconds.
        audio_kbps: Probed audio bitrate in kbps.
        fps: Source frames per second.
        src_h: Source height in pixels.
        opt_h: Target height for scaling.
        opt_fps: Target frames per second.

    Returns:
        Tuple of (success flag, error message).
    """
    brs = calculate_split_bitrates(target_size_mb, durs, audio_kbps)
    pa: Optional[subprocess.Popen[str]] = None
    pb: Optional[subprocess.Popen[str]] = None

    with tempfile.TemporaryDirectory(prefix="vidcomp_hw_", ignore_cleanup_errors=True) as temp_dir:
        p1_path = os.path.join(temp_dir, "p1.mp4")
        p2_path = os.path.join(temp_dir, "p2.mp4")
        list_path = os.path.join(temp_dir, "list.txt")

        try:
            cmd_a = build_single_pass_cmd(
                ffmpeg_exe=ffmpeg_exe,
                input_path=input_path,
                encoder=active_encoder,
                codec_type=codec_type,
                bitrate_k=brs[0],
                src_fps=fps,
                src_h=src_h,
                start=0.0,
                end=float(split_time),
                output_path=p1_path,
                tgt_h=opt_h,
                tgt_fps=opt_fps,
                audio_kbps=audio_kbps,
            )
            cmd_b = build_single_pass_cmd(
                ffmpeg_exe=ffmpeg_exe,
                input_path=input_path,
                encoder=active_encoder,
                codec_type=codec_type,
                bitrate_k=brs[1],
                src_fps=fps,
                src_h=src_h,
                start=float(split_time),
                end=None,
                output_path=p2_path,
                tgt_h=opt_h,
                tgt_fps=opt_fps,
                audio_kbps=audio_kbps,
            )

            clean_env = get_clean_env()
            pa = subprocess.Popen(cmd_a, stderr=subprocess.PIPE, text=True, bufsize=1, env=clean_env)
            pb = subprocess.Popen(cmd_b, stderr=subprocess.PIPE, text=True, bufsize=1, env=clean_env)

            console.print(Rule("[bold cyan]Encoding[/]", style="dim"))
            console.print()
            success = run_dual_progress(
                pa, pb, durs[0], durs[1], brs[0], brs[1], "Parallel Split Encoding"
            )
            console.print()

            if not success:
                return False, "Split encode failed"

            with console.status("[bold cyan]  Stitching segments...", spinner="dots"):
                with open(list_path, "w", encoding="utf-8") as lf:
                    lf.write(f"file '{p1_path}'\nfile '{p2_path}'")
                subprocess.run(
                    [
                        ffmpeg_exe, "-f", "concat", "-safe", "0", "-i", list_path,
                        "-c", "copy", "-movflags", "+faststart", "-y", output_path
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    env=clean_env,
                )
            console.print()

            return True, ""
        except KeyboardInterrupt:
            console.print("\n[bold red]Cancelling...[/]")
            raise
        except Exception as e:
            return False, f"Split single-pass error: {e}"
        finally:
            for p in (pa, pb):
                if p and p.poll() is None:
                    try:
                        p.kill()
                        p.wait(timeout=1.0)
                    except Exception:
                        pass


def _encode_cpu_2pass(
    ffmpeg_exe: str,
    input_path: str,
    output_path: str,
    active_encoder: str,
    codec_type: str,
    target_size_mb: float,
    duration: float,
    audio_kbps: int,
    fps: float,
    src_h: int,
    opt_h: int,
    opt_fps: float,
) -> Tuple[bool, str]:
    """Execute serial two-pass encoding for CPU software fallback (libx265, libx264).

    Args:
        ffmpeg_exe: Path to the ffmpeg executable.
        input_path: Source video path.
        output_path: Destination video path.
        active_encoder: FFmpeg encoder name.
        codec_type: "hevc" or "h264".
        target_size_mb: Target size in megabytes.
        duration: Total duration in seconds.
        audio_kbps: Probed audio bitrate in kbps.
        fps: Input frames per second.
        src_h: Source height in pixels.
        opt_h: Target height for scaling.
        opt_fps: Target frames per second.

    Returns:
        Tuple of (success flag, error message).
    """
    bitrate_k = calculate_video_bitrate(float(target_size_mb), duration, audio_kbps)
    clean_env = get_clean_env()

    with tempfile.TemporaryDirectory(prefix="vidcomp_cpu_", ignore_cleanup_errors=True) as temp_dir:
        pass_log_prefix = os.path.join(temp_dir, "cpu_pass")

        # PASS 1: Analysis
        cmd_p1 = build_cpu_pass_cmd(
            ffmpeg_exe=ffmpeg_exe,
            input_path=input_path,
            encoder=active_encoder,
            codec_type=codec_type,
            bitrate_k=bitrate_k,
            src_fps=fps,
            src_h=src_h,
            pass_num=1,
            pass_log_prefix=pass_log_prefix,
            output_path=output_path,
            tgt_h=opt_h,
            tgt_fps=opt_fps,
            audio_kbps=audio_kbps,
        )

        p1_proc: Optional[subprocess.Popen[str]] = None
        try:
            p1_proc = subprocess.Popen(
                cmd_p1,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=clean_env,
            )
            console.print(Rule("[bold cyan]Analysis[/]", style="dim"))
            console.print()
            ok1 = run_single_progress(p1_proc, duration, bitrate_k, pass_label="Pass 1/2 - Analysis")
            console.print()
            if not ok1:
                return False, "CPU Pass 1 Failed"
            console.print("[bold green]  Pass 1 complete.[/]\n")
        except KeyboardInterrupt:
            console.print("\n[bold red]Cancelling...[/]")
            raise
        finally:
            if p1_proc and p1_proc.poll() is None:
                try:
                    p1_proc.kill()
                    p1_proc.wait(timeout=1.0)
                except Exception:
                    pass

        # PASS 2: Encoding
        cmd_p2 = build_cpu_pass_cmd(
            ffmpeg_exe=ffmpeg_exe,
            input_path=input_path,
            encoder=active_encoder,
            codec_type=codec_type,
            bitrate_k=bitrate_k,
            src_fps=fps,
            src_h=src_h,
            pass_num=2,
            pass_log_prefix=pass_log_prefix,
            output_path=output_path,
            tgt_h=opt_h,
            tgt_fps=opt_fps,
            audio_kbps=audio_kbps,
        )

        p2_proc: Optional[subprocess.Popen[str]] = None
        try:
            p2_proc = subprocess.Popen(
                cmd_p2,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=clean_env,
            )
            console.print(Rule("[bold cyan]Encoding[/]", style="dim"))
            console.print()
            ok2 = run_single_progress(p2_proc, duration, bitrate_k, pass_label="Pass 2/2 - Encoding")
            console.print()
            if not ok2:
                return False, "CPU Pass 2 Failed"
            console.print("[bold green]  Pass 2 complete.[/]\n")
            return True, ""
        except KeyboardInterrupt:
            console.print("\n[bold red]Cancelling...[/]")
            raise
        finally:
            if p2_proc and p2_proc.poll() is None:
                try:
                    p2_proc.kill()
                    p2_proc.wait(timeout=1.0)
                except Exception:
                    pass


def compress_video(
    input_path: str,
    output_path: Optional[str] = None,
    target_size_mb: float = 100.0,
    codec_type: str = "hevc",
) -> Tuple[bool, str]:
    """Compress a video to an approximate target size ceiling.

    Chooses the best available encoder and uses either a parallel split
    for hardware encoders or a two-pass contiguous pipeline for CPU.

    Args:
        input_path: Path to the input video.
        output_path: Optional output path (defaults to .mp4 extension).
        target_size_mb: Desired target size ceiling in megabytes.
        codec_type: "hevc" or "h264".

    Returns:
        Tuple of (success, output_path_or_error_message).
    """
    start_t = time.time()
    ffmpeg_exe = get_resource_path("ffmpeg")
    clean_log_file()

    if not os.path.exists(input_path):
        return False, f"Input not found: {input_path}"

    info = get_video_info(input_path)
    if not info:
        return False, f"Failed to extract video info from: {input_path}"
    duration, orig_bytes, fps, audio_kbps, src_w, src_h = info

    if (orig_bytes / MB_TO_BYTES) <= target_size_mb:
        return False, f"Already smaller than target: {orig_bytes / MB_TO_BYTES:.2f} MB <= {target_size_mb} MB"

    if output_path is None:
        input_p = Path(input_path)
        sz_str = f"{int(target_size_mb)}" if float(target_size_mb).is_integer() else f"{target_size_mb}"
        output_path = str(input_p.with_name(f"{input_p.stem}_{sz_str}MB.mp4"))
    elif not output_path.lower().endswith(".mp4"):
        output_path = str(Path(output_path).with_suffix(".mp4"))

    # Decision Algorithm: Optimize Resolution and FPS
    opt_h, opt_fps = get_optimal_settings(target_size_mb, duration, src_w, src_h, fps)
    if opt_h != src_h or opt_fps != fps:
        opt_w = int(opt_h * (src_w / src_h))
        opt_w = opt_w if opt_w % 2 == 0 else opt_w + 1
        quality_info = f"{src_h}p{int(round(fps))} -> {opt_h}p{int(round(opt_fps))} [yellow](scaled {src_w}x{src_h} -> {opt_w}x{opt_h})[/]"
        console.print(f"[yellow]  Scale optimization:[/] [white]{src_w}x{src_h}@{fps:.2f}fps[/] -> [bold cyan]{opt_w}x{opt_h}@{opt_fps:.2f}fps[/] [dim](preserving visual clarity)[/]\n")
    else:
        quality_info = f"{src_h}p @ {fps:.2f} fps [dim](native)[/]"

    # Encoder Selection
    active_encoder, detection_results = select_best_encoder(codec_type)
    show_encoder_detection(detection_results)

    split_info: Optional[str] = None

    # Branch 1: Hardware Encoders (Parallel Split Single-Pass)
    if active_encoder not in ("libx265", "libx264"):
        split_time = get_smart_split_point(input_path, duration)
        durs_tuple = (split_time, duration - split_time)
        split_info = f"{durs_tuple[0]:.1f}s + {durs_tuple[1]:.1f}s"
        mode_str = "parallel split"

        ok, err = _encode_hw_split(
            ffmpeg_exe=ffmpeg_exe,
            input_path=input_path,
            output_path=output_path,
            active_encoder=active_encoder,
            codec_type=codec_type,
            target_size_mb=target_size_mb,
            split_time=split_time,
            durs=durs_tuple,
            audio_kbps=audio_kbps,
            fps=fps,
            src_h=src_h,
            opt_h=opt_h,
            opt_fps=opt_fps,
        )
        if not ok:
            clean_log_file()
            return False, err

    # Branch 2: CPU Fallback (Sequential Two-Pass Contiguous)
    else:
        mode_str = "CPU two-pass"
        ok, err = _encode_cpu_2pass(
            ffmpeg_exe=ffmpeg_exe,
            input_path=input_path,
            output_path=output_path,
            active_encoder=active_encoder,
            codec_type=codec_type,
            target_size_mb=target_size_mb,
            duration=duration,
            audio_kbps=audio_kbps,
            fps=fps,
            src_h=src_h,
            opt_h=opt_h,
            opt_fps=opt_fps,
        )
        if not ok:
            clean_log_file()
            return False, err

    clean_log_file()

    if os.path.exists(output_path):
        final_sz = get_file_size(output_path)
        elapsed_sec = time.time() - start_t
        final_br = int((final_sz * 8) / duration / 1000) if duration > 0 else 0

        show_result_panel(
            original_bytes=orig_bytes,
            final_bytes=final_sz,
            bitrate_k=final_br,
            elapsed_sec=elapsed_sec,
            encoder=active_encoder,
            mode=mode_str,
            split_info=split_info,
            quality_info=quality_info,
        )
        if sys.stdout.isatty():
            show_exit_countdown(3)
        return True, output_path

    return False, "Output missing"
