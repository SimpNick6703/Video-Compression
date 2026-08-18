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


def _encode_nvenc_2pass(
    ffmpeg_exe: str,
    input_path: str,
    output_path: str,
    active_encoder: str,
    codec_type: str,
    target_size_mb: int,
    duration: float,
    split_time: float,
    durs: Sequence[float],
    audio_kbps: int,
    fps: float,
    src_h: int,
    opt_h: int,
    opt_fps: float,
) -> Tuple[bool, str]:
    """Execute parallel 2-pass split encoding (NVENC / Windows AMF).

    Args:
        ffmpeg_exe: Path to the ffmpeg executable.
        input_path: Path to the input video.
        output_path: Path to the output video.
        active_encoder: FFmpeg encoder name.
        codec_type: "hevc" or "h264".
        target_size_mb: Target size in MB.
        duration: Total video duration in seconds.
        split_time: Split boundary in seconds.
        durs: Durations for [part1, part2].
        audio_kbps: Probed audio bitrate in kbps.
        fps: Source FPS.
        src_h: Source height.
        opt_h: Optimized target height.
        opt_fps: Optimized target FPS.

    Returns:
        Tuple of (success flag, error message).
    """
    brs = calculate_split_bitrates(target_size_mb, durs, audio_kbps)
    pa: Optional[subprocess.Popen[str]] = None
    pb: Optional[subprocess.Popen[str]] = None

    with tempfile.TemporaryDirectory(prefix="vidcomp_", ignore_cleanup_errors=True) as temp_dir:
        p1_path = os.path.join(temp_dir, "p1.mp4")
        p2_path = os.path.join(temp_dir, "p2.mp4")
        list_path = os.path.join(temp_dir, "list.txt")
        log_a = os.path.join(temp_dir, "log_part1")
        log_b = os.path.join(temp_dir, "log_part2")

        try:
            filters = []
            if opt_fps < fps:
                filters.append(f"fps={opt_fps}")
            if opt_h < src_h:
                filters.append(f"scale=-2:{opt_h}")

            if "nvenc" in active_encoder:
                if not filters:
                    hw_accel = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
                else:
                    hw_accel = ["-hwaccel", "cuda"]
                enc_params = ["-preset", "p5"]
            else:
                hw_accel = ["-hwaccel", "auto"]
                enc_params = ["-usage", "transcoding", "-quality", "quality"]

            base = [ffmpeg_exe] + hw_accel + ["-y", "-hide_banner", "-loglevel", "error", "-stats"]
            vf_args = ["-vf", ",".join(filters)] if filters else []

            # PASS 1: Analysis
            console.print(Rule("[bold cyan]Analysis[/]", style="dim"))
            console.print()
            cmd_a1 = base + ["-ss", "0", "-to", str(split_time), "-i", input_path, "-an"] + vf_args + ["-c:v", active_encoder] + enc_params + [
                "-b:v", f"{brs[0]}k", "-maxrate:v", f"{brs[0]}k", "-bufsize:v", f"{brs[0]*2}k",
                "-pass", "1", "-passlogfile", log_a, "-f", "null", "-"
            ]
            cmd_b1 = base + ["-ss", str(split_time), "-i", input_path, "-an"] + vf_args + ["-c:v", active_encoder] + enc_params + [
                "-b:v", f"{brs[1]}k", "-maxrate:v", f"{brs[1]}k", "-bufsize:v", f"{brs[1]*2}k",
                "-pass", "1", "-passlogfile", log_b, "-f", "null", "-"
            ]

            clean_env = get_clean_env()
            pa = subprocess.Popen(cmd_a1, stderr=subprocess.PIPE, text=True, bufsize=0, env=clean_env)
            pb = subprocess.Popen(cmd_b1, stderr=subprocess.PIPE, text=True, bufsize=0, env=clean_env)

            ok1 = run_dual_progress(pa, pb, durs[0], durs[1], brs[0], brs[1], "Pass 1/2 - Analysis")
            if not ok1:
                return False, "Pass 1 Failed"
            console.print("[bold green]  Pass 1 complete.[/]\n")

            # PASS 2: Encoding
            console.print(Rule("[bold cyan]Encoding[/]", style="dim"))
            console.print()
            cmd_a2 = base + ["-ss", "0", "-to", str(split_time), "-i", input_path] + vf_args + ["-c:v", active_encoder] + enc_params + [
                "-b:v", f"{brs[0]}k", "-maxrate:v", f"{brs[0]}k", "-bufsize:v", f"{brs[0]*2}k",
                "-pass", "2", "-passlogfile", log_a
            ]
            cmd_b2 = base + ["-ss", str(split_time), "-i", input_path] + vf_args + ["-c:v", active_encoder] + enc_params + [
                "-b:v", f"{brs[1]}k", "-maxrate:v", f"{brs[1]}k", "-bufsize:v", f"{brs[1]*2}k",
                "-pass", "2", "-passlogfile", log_b
            ]

            if codec_type == "hevc":
                cmd_a2.extend(["-tag:v", "hvc1"])
                cmd_b2.extend(["-tag:v", "hvc1"])
            elif codec_type == "h264":
                cmd_a2.extend(["-tag:v", "avc1"])
                cmd_b2.extend(["-tag:v", "avc1"])

            cmd_a2.extend(["-c:a", "copy", str(p1_path)])
            cmd_b2.extend(["-c:a", "copy", str(p2_path)])

            pa = subprocess.Popen(cmd_a2, stderr=subprocess.PIPE, text=True, bufsize=0, env=clean_env)
            pb = subprocess.Popen(cmd_b2, stderr=subprocess.PIPE, text=True, bufsize=0, env=clean_env)

            ok2 = run_dual_progress(pa, pb, durs[0], durs[1], brs[0], brs[1], "Pass 2/2 - Encoding")
            if not ok2:
                return False, "Pass 2 Failed"
            console.print("[bold green]  Pass 2 complete.[/]\n")

            # Stitching
            with console.status("[bold cyan]  Stitching segments...", spinner="dots"):
                with open(list_path, "w", encoding="utf-8") as lf:
                    lf.write(f"file '{p1_path}'\nfile '{p2_path}'")
                subprocess.run(
                    [ffmpeg_exe, "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", "-y", output_path],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=clean_env,
                )
            console.print()

            return True, ""
        except KeyboardInterrupt:
            console.print("\n[bold red]Cancelling...[/]")
            return False, "Cancelled by user"
        except Exception as e:
            return False, f"NVENC pass error: {e}"
        finally:
            for p in (pa, pb):
                if p and p.poll() is None:
                    try:
                        p.kill()
                        p.wait(timeout=1.0)
                    except Exception:
                        pass
            clean_log_file()


def _encode_hw_split(
    ffmpeg_exe: str,
    input_path: str,
    output_path: str,
    active_encoder: str,
    codec_type: str,
    target_size_mb: int,
    split_time: float,
    durs: Tuple[float, float],
    audio_kbps: int,
    fps: float,
    opt_h: int,
    opt_fps: float,
) -> Tuple[bool, str]:
    """Execute parallel split single-pass encoding for hardware encoders (VAAPI, QSV, VideoToolbox).

    Args:
        ffmpeg_exe: Path to the ffmpeg executable.
        input_path: Source video path.
        output_path: Destination file path.
        active_encoder: Active hardware encoder name (e.g., 'hevc_vaapi').
        codec_type: "hevc" or "h264".
        target_size_mb: Total target size in megabytes.
        split_time: Timestamp in seconds marking the segment boundary.
        durs: Tuple of (first_segment_duration, second_segment_duration) in seconds.
        audio_kbps: Probed audio bitrate in kbps.
        fps: Source frames per second.
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
                ffmpeg_exe, input_path, active_encoder, codec_type, brs[0], fps, 0.0, float(split_time), p1_path, opt_h, opt_fps
            )
            cmd_b = build_single_pass_cmd(
                ffmpeg_exe, input_path, active_encoder, codec_type, brs[1], fps, float(split_time), None, p2_path, opt_h, opt_fps
            )

            clean_env = get_clean_env()
            pa = subprocess.Popen(cmd_a, stderr=subprocess.PIPE, text=True, bufsize=0, env=clean_env)
            pb = subprocess.Popen(cmd_b, stderr=subprocess.PIPE, text=True, bufsize=0, env=clean_env)

            console.print(Rule("[bold cyan]Encoding[/]", style="dim"))
            console.print()
            success = run_dual_progress(
                pa, pb, durs[0], durs[1], brs[0], brs[1], "Split Single-Pass Encoding"
            )
            console.print()

            if not success:
                return False, "Split encode failed"

            with console.status("[bold cyan]  Stitching segments...", spinner="dots"):
                with open(list_path, "w", encoding="utf-8") as lf:
                    lf.write(f"file '{p1_path}'\nfile '{p2_path}'")
                subprocess.run(
                    [ffmpeg_exe, "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", "-y", output_path],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=clean_env,
                )
            console.print()

            return True, ""
        except KeyboardInterrupt:
            console.print("\n[bold red]Cancelling...[/]")
            return False, "Cancelled by user"
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


def _encode_cpu_single(
    ffmpeg_exe: str,
    input_path: str,
    output_path: str,
    active_encoder: str,
    codec_type: str,
    target_size_mb: int,
    duration: float,
    audio_kbps: int,
    fps: float,
    opt_h: int,
    opt_fps: float,
) -> Tuple[bool, str]:
    """Execute serial single-pass encoding for CPU software fallback (libx265, libx264).

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
        opt_h: Target height for scaling.
        opt_fps: Target frames per second.

    Returns:
        Tuple of (success flag, error message).
    """
    bitrate_k = calculate_video_bitrate(float(target_size_mb), duration, audio_kbps)
    process: Optional[subprocess.Popen[str]] = None
    try:
        cmd = build_single_pass_cmd(
            ffmpeg_exe=ffmpeg_exe,
            input_path=input_path,
            encoder=active_encoder,
            codec_type=codec_type,
            bitrate_k=bitrate_k,
            src_fps=fps,
            start=None,
            end=None,
            output_path=output_path,
            tgt_h=opt_h,
            tgt_fps=opt_fps,
        )
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            bufsize=0,
            env=get_clean_env(),
        )

        console.print(Rule("[bold cyan]Encoding[/]", style="dim"))
        console.print()
        success = run_single_progress(process, duration, bitrate_k, pass_label="Pass 1/1 - Encoding")
        console.print()
        return (True, "") if success else (False, "Encode Failed")
    except KeyboardInterrupt:
        console.print("\n[bold red]Cancelling...[/]")
        return False, "Cancelled by user"
    except Exception as e:
        return False, f"CPU encode error: {e}"
    finally:
        if process and process.poll() is None:
            try: process.kill()
            except Exception: pass


def compress_video(
    input_path: str,
    output_path: Optional[str] = None,
    target_size_mb: int = 100,
    codec_type: str = "hevc",
) -> Tuple[bool, str]:
    """Compress a video to an approximate target size.

    Chooses the best available encoder and uses either a parallel 2-pass split
    (NVENC / Windows AMF), a split single-pass for other hardware encoders,
    or a single unsplit pass for CPU (libx265 / libx264).

    Args:
        input_path: Path to the input video.
        output_path: Optional output path.
        target_size_mb: Desired approximate size in megabytes.
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
        output_path = str(input_p.with_name(f"{input_p.stem}_{target_size_mb}MB{input_p.suffix}"))

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
    mode_str = "Single-pass"

    # Branch 1: NVENC / AMF (Windows) Parallel 2-Pass
    if "nvenc" in active_encoder or ("amf" in active_encoder and sys.platform == "win32"):
        split_time = get_smart_split_point(input_path, duration)
        durs = (split_time, duration - split_time)
        split_info = f"{durs[0]:.1f}s + {durs[1]:.1f}s"
        mode_str = "2-pass split"

        ok, err = _encode_nvenc_2pass(
            ffmpeg_exe=ffmpeg_exe,
            input_path=input_path,
            output_path=output_path,
            active_encoder=active_encoder,
            codec_type=codec_type,
            target_size_mb=target_size_mb,
            duration=duration,
            split_time=split_time,
            durs=durs,
            audio_kbps=audio_kbps,
            fps=fps,
            src_h=src_h,
            opt_h=opt_h,
            opt_fps=opt_fps,
        )
        if not ok:
            clean_log_file()
            return False, err

    # Branch 2: Split Single-Pass for Other HW Encoders (VAAPI, QSV, VideoToolbox, Linux AMF)
    elif active_encoder not in ["libx265", "libx264"]:
        split_time = get_smart_split_point(input_path, duration)
        durs_tuple = (split_time, duration - split_time)
        split_info = f"{durs_tuple[0]:.1f}s + {durs_tuple[1]:.1f}s"
        mode_str = "split single-pass"

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
            opt_h=opt_h,
            opt_fps=opt_fps,
        )
        if not ok:
            clean_log_file()
            return False, err

    # Branch 3: Serial Single-Pass CPU Fallback (libx265 / libx264)
    else:
        mode_str = "CPU single-pass"
        ok, err = _encode_cpu_single(
            ffmpeg_exe=ffmpeg_exe,
            input_path=input_path,
            output_path=output_path,
            active_encoder=active_encoder,
            codec_type=codec_type,
            target_size_mb=target_size_mb,
            duration=duration,
            audio_kbps=audio_kbps,
            fps=fps,
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
        show_exit_countdown(3)
        return True, output_path

    return False, "Output missing"
