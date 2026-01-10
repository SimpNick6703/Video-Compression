import sys
import subprocess
import time
import os
import re
import math
import json
import threading
import tempfile
import shutil
from pathlib import Path
from typing import Tuple, Optional, List

# --- Constants ---
MB_TO_BYTES = 1024 * 1024
MB_TO_BITS = 8 * 1024 * 1024
BITRATE_SAFETY_FACTOR = 0.90
LOG_FILES_TO_CLEAN = ["ffmpeg2pass.log", "ffmpeg2pass-0.log", "ffmpeg2pass-0.log.mbtree"] 

# --- Helpers ---

def get_resource_path(filename: str) -> str:
    """Resolve the absolute path to bundled resources.

    Args:
        filename: Base executable or file name (e.g., "ffmpeg").

    Returns:
        Absolute path to the resource, respecting PyInstaller bundling.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS # type: ignore
        if sys.platform == 'win32' and not filename.lower().endswith('.exe'):
            filename = f"{filename}.exe"
        return os.path.join(base_path, filename)
    return filename

def get_file_size(file_path: str) -> int:
    """Return the file size in bytes.

    Args:
        file_path: Path to the file.

    Returns:
        File size in bytes.
    """
    return os.path.getsize(file_path)

def format_size(size_bytes: int) -> str:
    """Format a byte count as a human-readable string.

    Args:
        size_bytes: Size value in bytes.

    Returns:
        A concise human-readable string (B, KB, MB).
    """
    if size_bytes < 1024: return f"{size_bytes} B"
    elif size_bytes < MB_TO_BYTES: return f"{size_bytes/1024:.2f} KB"
    else: return f"{size_bytes/MB_TO_BYTES:.2f} MB"

def clean_log_file(prefixes: Optional[List[str]] = None) -> None:
    """Remove temporary FFmpeg log files.

    Args:
        prefixes: Optional list of 2-pass log prefixes to clean as well.
    """
    for log_file in LOG_FILES_TO_CLEAN:
        try:
            if os.path.exists(log_file): os.remove(log_file)
        except OSError: pass
    if prefixes:
        for p in prefixes:
            for ext in ["-0.log", "-0.log.mbtree"]:
                try:
                    log_path = p + ext
                    if os.path.exists(log_path): os.remove(log_path)
                except OSError: pass

def check_encoder_available(encoder_name: str) -> bool:
    """Check if a specific FFmpeg encoder can be used.

    Args:
        encoder_name: FFmpeg encoder name (e.g., "hevc_nvenc").

    Returns:
        True if a short test encode succeeds, else False.
    """
    ffmpeg_exe = get_resource_path("ffmpeg")
    try:
        # VAAPI often requires hwupload for software sources
        vf_args = ["-vf", "format=nv12,hwupload"] if encoder_name == "hevc_vaapi" else []
        pre_args = ["-init_hw_device", "vaapi"] if encoder_name == "hevc_vaapi" else []
        
        cmd = [ffmpeg_exe, "-hide_banner", "-v", "error"] + pre_args + [
            "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=1:d=0.1", 
            "-vframes", "1", "-c:v", encoder_name
        ] + vf_args + ["-f", "null", "-"]
        
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False

def select_best_encoder() -> str:
    """Detect the best available encoder based on OS and Hardware.

    Returns:
        Encoder name (e.g., 'hevc_nvenc') or 'libx265' if none found.
    """
    # 1. Determine priority chain based on OS to avoid useless checks
    if sys.platform.startswith("linux"):
        # Linux: Nvidia or VAAPI (Intel/AMD)
        priority_chain = ["hevc_nvenc", "hevc_vaapi"]
    elif sys.platform == "darwin":
        # MacOS: VideoToolbox (Standard)
        priority_chain = ["hevc_videotoolbox"]
    elif sys.platform == "win32":
        # Windows: Nvidia -> AMD (AMF) -> Intel (QSV)
        priority_chain = ["hevc_nvenc", "hevc_amf", "hevc_qsv"]
    else:
        # Fallback: Check everything
        priority_chain = ["hevc_nvenc", "hevc_vaapi", "hevc_videotoolbox", "hevc_amf", "hevc_qsv"]

    print(f"OS: {sys.platform}. Checking encoders: {', '.join(priority_chain)}...")

    # 2. Check each candidate
    for enc in priority_chain:
        is_available = check_encoder_available(enc)
        print(f"  {enc}: {'Available' if is_available else 'Unavailable'}")
        if is_available:
            return enc
    
    print("  No hardware encoder found. Fallback to CPU.")
    return "libx265"

def get_video_info(input_path: str) -> Optional[Tuple[float, int, float, int]]:
    """Probe video metadata.

    Args:
        input_path: Path to the input media file.

    Returns:
        Tuple of (duration_seconds, file_size_bytes, fps, audio_kbps), or None on failure.
    """
    ffprobe_exe = get_resource_path("ffprobe")
    try:
        cmd_base = [ffprobe_exe, "-v", "error", "-select_streams", "v:0", "-of", "default=noprint_wrappers=1:nokey=1"]
        fps_out = subprocess.check_output(cmd_base + ["-show_entries", "stream=avg_frame_rate", input_path], text=True).strip()
        dur_out = subprocess.check_output(cmd_base + ["-show_entries", "format=duration", input_path], text=True).strip()
        
        cmd_aud = [ffprobe_exe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=bit_rate", "-of", "default=noprint_wrappers=1:nokey=1", input_path]
        try:
            aud_out = subprocess.check_output(cmd_aud, text=True).strip()
            audio_bps = int(aud_out) if aud_out.isdigit() else 128000
        except subprocess.CalledProcessError:
            audio_bps = 128000 # Default if no audio stream found or probe fails
            
        if not fps_out or '/' not in fps_out: return None
        num, den = map(int, fps_out.split('/'))
        fps = num / den
        
        return float(dur_out), get_file_size(input_path), fps, math.ceil(audio_bps / 1000)
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None

def get_smart_split_point(input_path: str, duration: float) -> float:
    """Find a keyframe-aligned split point near the middle.

    Args:
        input_path: Path to the input media file.
        duration: Total duration in seconds.

    Returns:
        Timestamp in seconds to split the encode.
    """
    print("Analyzing for Smart Split point...")
    try:
        cmd = [get_resource_path("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,size,flags", "-of", "json", input_path]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        packets = json.loads(res.stdout).get('packets', [])
        
        target = sum(int(p.get('size', 0)) for p in packets) / 2
        curr, last_k = 0, 0.0
        
        for p in packets:
            curr += int(p.get('size', 0))
            if 'K' in p.get('flags', ''): last_k = float(p.get('pts_time', 0))
            if curr >= target: return last_k if last_k > 0 else duration/2
    except (json.JSONDecodeError, KeyError, ValueError, OSError): pass
    return duration / 2

# --- Progress Tracking ---

class ProgressTracker:
    """Track progress for two parallel encoding segments."""

    def __init__(self, duration_a: float, duration_b: float) -> None:
        self.dur_a, self.dur_b = duration_a, duration_b
        self.total_dur = duration_a + duration_b
        self.time_a = self.time_b = 0.0
        self.fps_a = self.fps_b = 0.0
        self.spd_a = self.spd_b = 0.001
        self.lock = threading.Lock()

    def update(self, is_a: bool, time_val: float, fps_val: float, speed_val: float) -> None:
        """Update stats for a segment.

        Args:
            is_a: True for first segment, False for second.
            time_val: Last parsed encode time in seconds.
            fps_val: Current frames per second.
            speed_val: Current encode speed multiplier.
        """
        with self.lock:
            if is_a:
                self.time_a = time_val
                if fps_val: self.fps_a = fps_val
                if speed_val: self.spd_a = speed_val
            else:
                self.time_b = time_val
                if fps_val: self.fps_b = fps_val
                if speed_val: self.spd_b = speed_val

    def get_stats(self) -> Tuple[float, float, int]:
        """Compute aggregate progress, fps, and ETA.

        Returns:
            Tuple of (progress_percent, total_fps, eta_seconds).
        """
        with self.lock:
            t_a = min(self.time_a, self.dur_a)
            t_b = min(self.time_b, self.dur_b)
            prog = min(100, ((t_a + t_b) / self.total_dur) * 100)
            fps = self.fps_a + self.fps_b
            rem_a = max(0, self.dur_a - self.time_a)
            rem_b = max(0, self.dur_b - self.time_b)
            eta = max(rem_a / self.spd_a, rem_b / self.spd_b)
            return prog, fps, int(eta)

def monitor_process(process: subprocess.Popen, tracker: ProgressTracker, is_a: bool) -> None:
    """Monitor an FFmpeg process and update progress.

    Args:
        process: Running FFmpeg process (stderr expected with progress lines).
        tracker: Shared tracker to update.
        is_a: True if this process represents the first segment.
    """
    re_time = re.compile(r'time=\s*(\d+:\d+:\d+\.\d+)')
    re_fps = re.compile(r'fps=\s*(\d+\.?\d*)')
    re_speed = re.compile(r'speed=\s*(\d+\.?\d*)x')
    
    buf = ""
    while True:
        char = process.stderr.read(1) # type: ignore
        if not char: break
        
        if char in ('\r', '\n'):
            if buf.strip():
                try:
                    t_match = re_time.search(buf)
                    f_match = re_fps.search(buf)
                    s_match = re_speed.search(buf)
                    
                    if t_match:
                        h, m, s = map(float, t_match.group(1).split(':'))
                        secs = h*3600 + m*60 + s
                        
                        fps = float(f_match.group(1)) if f_match else 0.0
                        spd = float(s_match.group(1)) if s_match else 0.001
                        
                        tracker.update(is_a, secs, fps, spd)
                except (ValueError, AttributeError, IndexError): pass
            buf = ""
        else:
            buf += char

# --- Main Logic ---

def encode_single_pass_hw(
    ffmpeg_exe: str,
    input_path: str,
    output_path: str,
    encoder: str,
    bitrate_k: int,
    fps: float,
    duration: float,
) -> bool:
    """Encode using a single pass for non-NVENC paths.

    Args:
        ffmpeg_exe: Path to the ffmpeg executable.
        input_path: Source video path.
        output_path: Destination video path.
        encoder: FFmpeg video encoder name.
        bitrate_k: Target video bitrate in kbps.
        fps: Input frames per second.
        duration: Total duration in seconds (for progress reporting).

    Returns:
        True on success, False on failure.
    """
    cmd = build_single_pass_cmd(
        ffmpeg_exe=ffmpeg_exe,
        input_path=input_path,
        encoder=encoder,
        bitrate_k=bitrate_k,
        fps=fps,
        start=None,
        end=None,
        output_path=output_path,
    )

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    
    # Simple single-process monitor
    pat = re.compile(r'frame=\s*(\d+).*?fps=\s*(\d+\.?\d*).*?time=(\d+:\d+:\d+\.\d+).*?speed=\s*(\d+\.?\d*)x')
    buf = ""
    last_speed = 0.001
    
    try:
        while True:
            byte = process.stderr.read(1) # type: ignore
            if not byte: break
            char = byte.decode('utf-8', errors='ignore')
            if char == '\r' or char == '\n':
                match = pat.search(buf)
                if match:
                    cur_fps = float(match.group(2)) if match.group(2) else 0
                    h, m, s = map(float, match.group(3).split(':'))
                    cur_time = h * 3600 + m * 60 + s
                    speed = float(match.group(4)) if match.group(4) else last_speed
                    if speed > 0: last_speed = speed
                    prog = min(100, (cur_time / duration) * 100)
                    eta = max(0, duration - cur_time) / last_speed
                    eta_str = f"{int(eta//60)}m{int(eta%60)}s" if eta < 3600 else f"{int(eta//3600)}h{int((eta%3600)//60)}m"
                    print(f"\rProgress: {prog:.1f}% | FPS: {cur_fps:.1f} | Speed: {speed:.2f}x | ETA: {eta_str}   ", end="")
                buf = ""
            else:
                buf += char
    finally:
        process.wait()
    
    print()
    return process.returncode == 0


def build_single_pass_cmd(
    ffmpeg_exe: str,
    input_path: str,
    encoder: str,
    bitrate_k: int,
    fps: float,
    start: Optional[float],
    end: Optional[float],
    output_path: str,
) -> List[str]:
    """Build a single-pass FFmpeg command for the requested encoder.

    Args:
        ffmpeg_exe: Path to the ffmpeg executable.
        input_path: Source video path.
        encoder: FFmpeg encoder name.
        bitrate_k: Target bitrate in kbps.
        fps: Source frames per second.
        start: Optional start time for segmenting.
        end: Optional end time for segmenting.
        output_path: Destination video path.

    Returns:
        A command list ready for subprocess execution.
    """
    cmd: List[str] = [ffmpeg_exe, "-y"]

    if encoder == "hevc_vaapi":
        cmd.extend(["-init_hw_device", "vaapi", "-hwaccel", "vaapi"])
    
    elif encoder == "hevc_amf":
        cmd.extend(["-hwaccel", "d3d11va", "-hwaccel_output_format", "d3d11"])
        
    elif encoder == "hevc_qsv":
        cmd.extend(["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"])

    if start is not None:
        cmd.extend(["-ss", str(start)])
    if end is not None:
        cmd.extend(["-to", str(end)])

    cmd.extend(["-i", input_path, "-c:v", encoder, "-b:v", f"{bitrate_k}k"])

    if encoder == "hevc_amf":
        cmd.extend(["-usage", "transcoding", "-quality", "balanced", "-rc", "cbr"])
    elif encoder == "hevc_qsv":
        cmd.extend(["-load_plugin", "hevc_hw", "-preset", "medium"])
    elif encoder == "hevc_vaapi":
        cmd.extend(["-vf", "format=nv12,hwupload"])
    elif encoder == "hevc_videotoolbox":
        cmd.extend(["-allow_sw", "1", "-realtime", "0"])
    elif encoder == "libx265":
        cmd.extend(["-preset", "medium", "-tag:v", "hvc1", "-filter:v", f"fps={fps}"])

    cmd.extend(["-maxrate:v", f"{bitrate_k}k", "-bufsize:v", f"{bitrate_k*2}k"])
    cmd.extend(["-c:a", "copy", "-loglevel", "error", "-stats", output_path])
    return cmd


def encode_split_single_pass_hw(
    ffmpeg_exe: str,
    input_path: str,
    output_path: str,
    encoder: str,
    bitrates_k: Tuple[int, int],
    fps: float,
    durations: Tuple[float, float],
    split_time: float,
) -> Tuple[bool, str]:
    """Run split single-pass encoding for hardware encoders other than NVENC.

    Args:
        ffmpeg_exe: Path to the ffmpeg executable.
        input_path: Source video path.
        output_path: Destination file path.
        encoder: Active hardware encoder.
        bitrates_k: Tuple of bitrates (kbps) for first and second segments.
        fps: Source frames per second.
        durations: Durations of the first and second segments.
        split_time: Timestamp marking the segment boundary.

    Returns:
        Tuple of (success flag, error message when unsuccessful).
    """
    temp_dir = tempfile.mkdtemp(prefix="vidcomp_hw_")
    p1_path = os.path.join(temp_dir, "p1.mp4")
    p2_path = os.path.join(temp_dir, "p2.mp4")
    list_path = os.path.join(temp_dir, "list.txt")

    try:
        cmd_a = build_single_pass_cmd(ffmpeg_exe, input_path, encoder, bitrates_k[0], fps, 0, split_time, p1_path)
        cmd_b = build_single_pass_cmd(ffmpeg_exe, input_path, encoder, bitrates_k[1], fps, split_time, None, p2_path)

        pa = subprocess.Popen(cmd_a, stderr=subprocess.PIPE, text=True, bufsize=0)
        pb = subprocess.Popen(cmd_b, stderr=subprocess.PIPE, text=True, bufsize=0)

        tracker = ProgressTracker(durations[0], durations[1])
        t1 = threading.Thread(target=monitor_process, args=(pa, tracker, True))
        t2 = threading.Thread(target=monitor_process, args=(pb, tracker, False))
        t1.start(); t2.start()

        while pa.poll() is None or pb.poll() is None:
            prog, fps_total, eta = tracker.get_stats()
            speed = fps_total / fps if fps > 0 else 0
            print(f"\rProg: {prog:.1f}% | FPS: {fps_total:.1f} | Speed: {speed:.2f}x | ETA: {eta//3600:02}:{(eta%3600)//60:02}:{eta%60:02}   ", end="")
            time.sleep(0.5)

        t1.join(); t2.join()

        if pa.returncode != 0 or pb.returncode != 0:
            return False, "Split encode failed"

        print("\nStitching...")
        with open(list_path, "w") as lf:
            lf.write(f"file '{p1_path}'\nfile '{p2_path}'")

        try:
            subprocess.run([ffmpeg_exe, "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", "-y", output_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            return False, "Stitching failed"

        return True, ""
    finally:
        try:
            shutil.rmtree(temp_dir)
        except OSError:
            pass

def compress_video(input_path: str, output_path: Optional[str] = None, target_size_mb: int = 100) -> Tuple[bool, str]:
    """Compress a video to an approximate target size.

    Chooses the best available encoder and uses either a parallel 2-pass split
    (NVENC), a split single-pass for other hardware encoders, or a single
    unsplit pass for CPU (libx265).

    Args:
        input_path: Path to the input video.
        output_path: Optional output path; defaults to "<name>_<MB>MB<ext>".
        target_size_mb: Desired approximate size in megabytes.

    Returns:
        Tuple of (success, output_path_or_error_message).
    """
    start_t = time.time()
    ffmpeg_exe = get_resource_path("ffmpeg")
    clean_log_file()
    
    if not os.path.exists(input_path): return False, f"Input not found: {input_path}"
    
    info = get_video_info(input_path)
    if not info: return False, f"Failed to extract video info from: {input_path}"
    duration, orig_bytes, fps, audio_kbps = info
    
    if (orig_bytes / MB_TO_BYTES) <= target_size_mb:
        return False, f"Already smaller: {orig_bytes/MB_TO_BYTES:.2f} MB"

    if output_path is None:
        output_path = str(Path(input_path).with_name(f"{Path(input_path).stem}_{target_size_mb}MB{Path(input_path).suffix}"))

    active_encoder = select_best_encoder()
    print(f"Active Encoder: {active_encoder}")

    # --- NVENC SPECIFIC: PARALLEL 2-PASS ---
    if active_encoder == "hevc_nvenc":
        split_time = get_smart_split_point(input_path, duration)
        print(f"Splitting at {split_time:.2f}s")
        
        durs = [split_time, duration - split_time]
        brs = []
        
        tgt_part_mb = target_size_mb / 2
        for d in durs:
            audio_mb = (audio_kbps * d * 1000) / 8 / MB_TO_BYTES
            video_mb = max(0.5, tgt_part_mb - audio_mb)
            br_k = math.floor(((video_mb * MB_TO_BITS) / d / 1000) * BITRATE_SAFETY_FACTOR)
            brs.append(br_k)

        print(f"Worker 1: {brs[0]}k | Worker 2: {brs[1]}k")
        
        temp_dir = tempfile.mkdtemp(prefix="vidcomp_")
        p1_path = os.path.join(temp_dir, "p1.mp4")
        p2_path = os.path.join(temp_dir, "p2.mp4")
        list_path = os.path.join(temp_dir, "list.txt")
        log_a = os.path.join(temp_dir, "log_part1")
        log_b = os.path.join(temp_dir, "log_part2")
        
        try:
            base = [ffmpeg_exe, "-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-y", "-hide_banner", "-loglevel", "error", "-stats"]

            # PASS 1
            print("Parallel Pass 1/2: Analysis...")
            cmd_a1 = base + ["-ss", "0", "-to", str(split_time), "-i", input_path, "-c:v", "hevc_nvenc", "-preset", "p5", 
                            "-b:v", f"{brs[0]}k", "-maxrate:v", f"{brs[0]}k", "-bufsize:v", f"{brs[0]*2}k",
                            "-pass", "1", "-passlogfile", log_a, "-f", "null", "NUL" if os.name=='nt' else "/dev/null"]
            
            cmd_b1 = base + ["-ss", str(split_time), "-i", input_path, "-c:v", "hevc_nvenc", "-preset", "p5", 
                            "-b:v", f"{brs[1]}k", "-maxrate:v", f"{brs[1]}k", "-bufsize:v", f"{brs[1]*2}k",
                            "-pass", "1", "-passlogfile", log_b, "-f", "null", "NUL" if os.name=='nt' else "/dev/null"]

            pa = subprocess.Popen(cmd_a1, stderr=subprocess.PIPE, text=True, bufsize=0)
            pb = subprocess.Popen(cmd_b1, stderr=subprocess.PIPE, text=True, bufsize=0)
            
            trk = ProgressTracker(durs[0], durs[1])
            t1 = threading.Thread(target=monitor_process, args=(pa, trk, True))
            t2 = threading.Thread(target=monitor_process, args=(pb, trk, False))
            t1.start(); t2.start()
            
            while pa.poll() is None or pb.poll() is None:
                p, f, e = trk.get_stats()
                print(f"\rProg: {p:.1f}% | FPS: {f:.1f}   ", end="")
                time.sleep(0.5)
            t1.join(); t2.join()
            
            if pa.returncode != 0 or pb.returncode != 0: return False, "Pass 1 Failed"
            print("\nPass 1 Complete.")

            # PASS 2
            print("Parallel Pass 2/2: Encoding...")
            trk = ProgressTracker(durs[0], durs[1])
            cmd_a2 = base + ["-ss", "0", "-to", str(split_time), "-i", input_path, "-c:v", "hevc_nvenc", "-preset", "p5", 
                            "-b:v", f"{brs[0]}k", "-maxrate:v", f"{brs[0]}k", "-bufsize:v", f"{brs[0]*2}k",
                            "-pass", "2", "-passlogfile", log_a, "-c:a", "copy", str(p1_path)]
            
            cmd_b2 = base + ["-ss", str(split_time), "-i", input_path, "-c:v", "hevc_nvenc", "-preset", "p5", 
                            "-b:v", f"{brs[1]}k", "-maxrate:v", f"{brs[1]}k", "-bufsize:v", f"{brs[1]*2}k",
                            "-pass", "2", "-passlogfile", log_b, "-c:a", "copy", str(p2_path)]

            pa = subprocess.Popen(cmd_a2, stderr=subprocess.PIPE, text=True, bufsize=0)
            pb = subprocess.Popen(cmd_b2, stderr=subprocess.PIPE, text=True, bufsize=0)
            
            t1 = threading.Thread(target=monitor_process, args=(pa, trk, True))
            t2 = threading.Thread(target=monitor_process, args=(pb, trk, False))
            t1.start(); t2.start()
            
            while pa.poll() is None or pb.poll() is None:
                p, f, e = trk.get_stats()
                speed = f / fps if fps > 0 else 0
                print(f"\rProg: {p:.1f}% | FPS: {f:.1f} | Speed: {speed:.2f}x | ETA: {e//3600:02}:{(e%3600)//60:02}:{e%60:02}   ", end="")
                time.sleep(0.5)
            t1.join(); t2.join()

            if pa.returncode != 0 or pb.returncode != 0: return False, "Pass 2 Failed"
            
            print("\nStitching...")
            with open(list_path, "w") as lf: lf.write(f"file '{p1_path}'\nfile '{p2_path}'")
            try:
                subprocess.run([ffmpeg_exe, "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", "-y", output_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                return False, "Stitching Failed"
        except KeyboardInterrupt:
            print("\nCancelling...")
            try: pa.kill() 
            except: pass
            try: pb.kill() 
            except: pass
            return False, "Cancelled"
        finally:
            try: shutil.rmtree(temp_dir)
            except OSError: pass
            clean_log_file()

    # --- SPLIT SINGLE-PASS FOR OTHER HW ENCODERS ---
    elif active_encoder in {"hevc_vaapi", "hevc_videotoolbox", "hevc_amf", "hevc_qsv"}:
        split_time = get_smart_split_point(input_path, duration)
        print(f"Splitting at {split_time:.2f}s")

        durs = (split_time, duration - split_time)
        brs: List[int] = []

        tgt_part_mb = target_size_mb / 2
        for seg_dur in durs:
            audio_mb = (audio_kbps * seg_dur * 1000) / 8 / MB_TO_BYTES
            video_mb = max(0.5, tgt_part_mb - audio_mb)
            br_k = math.floor(((video_mb * MB_TO_BITS) / seg_dur / 1000) * BITRATE_SAFETY_FACTOR)
            brs.append(br_k)

        print(f"Worker 1: {brs[0]}k | Worker 2: {brs[1]}k")
        ok, err = encode_split_single_pass_hw(ffmpeg_exe, input_path, output_path, active_encoder, (brs[0], brs[1]), fps, durs, split_time)
        if not ok:
            clean_log_file()
            return False, err

    # --- SERIAL SINGLE-PASS (CPU) ---
    else:
        tgt_bits = target_size_mb * MB_TO_BITS
        vid_bits = max(0, tgt_bits - (audio_kbps * 1000 * duration))
        vid_br = math.floor(((vid_bits / duration) / 1000) * BITRATE_SAFETY_FACTOR)
        
        print(f"Encoding Single Pass. Target: {vid_br}k")
        success = encode_single_pass_hw(ffmpeg_exe, input_path, output_path, active_encoder, vid_br, fps, duration)
        if not success: return False, "Encode Failed"

    clean_log_file()
    
    if os.path.exists(output_path):
        final_sz = get_file_size(output_path)
        print(f"Finished. Final size: {format_size(final_sz)} ({(1-final_sz/orig_bytes)*100:.1f}% reduced)")
        print(f"Time: {time.time()-start_t:.1f}s")
        return True, output_path
    
    return False, "Output missing"

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python script.py <input> [output] [size_mb]")
        sys.exit(1)

    input_file = sys.argv[1]
    
    # Defaults
    output_file = None
    target_mb = 100

    for arg in sys.argv[2:]:
        if arg.isdigit():
            target_mb = int(arg)
        else:
            output_file = arg

    success, result = compress_video(input_file, output_file, target_size_mb=target_mb)
    if not success: sys.exit(1)