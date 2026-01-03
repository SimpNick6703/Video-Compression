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
    """
    Get absolute path to resource, works for dev and for PyInstaller.
    When the app is frozen (onefile), it looks for ffmpeg/ffprobe
    inside the temporary folder (_MEIPASS) instead of the system PATH.
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
        if sys.platform == 'win32' and not filename.lower().endswith('.exe'):
            filename = f"{filename}.exe"
        return os.path.join(base_path, filename)
    return filename

def get_file_size(file_path: str) -> int:
    """Return file size in bytes."""
    return os.path.getsize(file_path)

def format_size(size_bytes: int) -> str:
    """Format byte size to human-readable string."""
    if size_bytes < 1024: return f"{size_bytes} B"
    elif size_bytes < MB_TO_BYTES: return f"{size_bytes/1024:.2f} KB"
    else: return f"{size_bytes/MB_TO_BYTES:.2f} MB"

def clean_log_file(prefixes=None):
    """Remove temporary FFmpeg log files."""
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

def check_nvenc_available() -> Tuple[bool, Optional[str]]:
    """
    Check if NVENC is usable by attempting a dummy encoding.
    Returns (True, None) if hardware is present and working.
    Returns (False, error_message) if NVENC is unavailable.
    """
    ffmpeg_exe = get_resource_path("ffmpeg")
    try:
        cmd = [
            ffmpeg_exe, "-hide_banner", "-v", "error", "-f", "lavfi", 
            "-i", "color=c=black:s=1280x720:r=1:d=0.1", "-vframes", "1",
            "-c:v", "hevc_nvenc", "-f", "null", "-"
        ]
        result = subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        return True, None
    except subprocess.CalledProcessError as e:
        return False, f"NVENC encoding failed: {e.stderr.strip() if e.stderr else 'Unknown error'}"
    except FileNotFoundError:
        return False, f"FFmpeg not found at: {ffmpeg_exe}"
    except OSError as e:
        return False, f"OS error during NVENC check: {e}"

def get_video_info(input_path: str) -> Optional[Tuple[float, int, float, int]]:
    """
    Extract video metadata: duration, file size, fps, and audio bitrate.
    Returns None if extraction fails.
    """
    ffprobe_exe = get_resource_path("ffprobe")
    try:
        cmd_base = [ffprobe_exe, "-v", "error", "-select_streams", "v:0", "-of", "default=noprint_wrappers=1:nokey=1"]
        fps_out = subprocess.check_output(cmd_base + ["-show_entries", "stream=avg_frame_rate", input_path], text=True).strip()
        dur_out = subprocess.check_output(cmd_base + ["-show_entries", "format=duration", input_path], text=True).strip()
        
        cmd_aud = [ffprobe_exe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=bit_rate", "-of", "default=noprint_wrappers=1:nokey=1", input_path]
        aud_out = subprocess.check_output(cmd_aud, text=True).strip()

        if not fps_out or '/' not in fps_out: return None
        num, den = map(int, fps_out.split('/'))
        fps = num / den
        
        audio_bps = int(aud_out) if aud_out.isdigit() else 128000
        return float(dur_out), get_file_size(input_path), fps, math.ceil(audio_bps / 1000)
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None

def get_smart_split_point(input_path: str, duration: float) -> float:
    """
    Find optimal keyframe-aligned split point for parallel encoding.
    Analyzes packet sizes to split video at approximately half the data size.
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
    def __init__(self, duration_a, duration_b):
        self.dur_a, self.dur_b = duration_a, duration_b
        self.total_dur = duration_a + duration_b
        self.time_a = self.time_b = 0.0
        self.fps_a = self.fps_b = 0.0
        self.spd_a = self.spd_b = 0.001
        self.lock = threading.Lock()

    def update(self, is_a, time_val, fps_val, speed_val):
        with self.lock:
            if is_a:
                self.time_a = time_val
                if fps_val: self.fps_a = fps_val
                if speed_val: self.spd_a = speed_val
            else:
                self.time_b = time_val
                if fps_val: self.fps_b = fps_val
                if speed_val: self.spd_b = speed_val

    def get_stats(self):
        with self.lock:
            t_a = min(self.time_a, self.dur_a)
            t_b = min(self.time_b, self.dur_b)
            prog = min(100, ((t_a + t_b) / self.total_dur) * 100)
            fps = self.fps_a + self.fps_b
            rem_a = max(0, self.dur_a - self.time_a)
            rem_b = max(0, self.dur_b - self.time_b)
            eta = max(rem_a / self.spd_a, rem_b / self.spd_b)
            return prog, fps, int(eta)

def monitor_process(process, tracker, is_a):
    """Monitor FFmpeg process stderr and update progress tracker."""
    re_time = re.compile(r'time=\s*(\d+:\d+:\d+\.\d+)')
    re_fps = re.compile(r'fps=\s*(\d+\.?\d*)')
    re_speed = re.compile(r'speed=\s*(\d+\.?\d*)x')
    
    buf = ""
    while True:
        char = process.stderr.read(1)
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

def compress_video(input_path: str, output_path: Optional[str] = None, target_size_mb: int = 100) -> Tuple[bool, str]:
    """Compress video to target file size using NVENC (GPU) or libx265 (CPU)."""
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

    use_nvenc, nvenc_error = check_nvenc_available()
    if not use_nvenc and nvenc_error:
        print(f"NVENC unavailable: {nvenc_error}")
    
    # --- PARALLEL NVENC (2-PASS) ---
    if use_nvenc:
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
        
        # Create temp directory for intermediate files
        temp_dir = tempfile.mkdtemp(prefix="vidcomp_")
        p1_path = os.path.join(temp_dir, "p1.mp4")
        p2_path = os.path.join(temp_dir, "p2.mp4")
        list_path = os.path.join(temp_dir, "list.txt")
        log_a = os.path.join(temp_dir, "log_part1")
        log_b = os.path.join(temp_dir, "log_part2")
        
        try:
            base = [ffmpeg_exe, "-hwaccel", "cuda", "-y", "-hide_banner", "-loglevel", "info", "-stats"]

            # PASS 1
            print("Parallel Pass 1/2: Analysis...")
            cmd_a1 = base + ["-ss", "0", "-to", str(split_time), "-i", input_path, "-c:v", "hevc_nvenc", "-preset", "p5", "-b:v", f"{brs[0]}k", "-pass", "1", "-passlogfile", log_a, "-f", "null", "NUL" if os.name=='nt' else "/dev/null"]
            cmd_b1 = base + ["-ss", str(split_time), "-i", input_path, "-c:v", "hevc_nvenc", "-preset", "p5", "-b:v", f"{brs[1]}k", "-pass", "1", "-passlogfile", log_b, "-f", "null", "NUL" if os.name=='nt' else "/dev/null"]

            pa = subprocess.Popen(cmd_a1, stderr=subprocess.PIPE, bufsize=0)
            pb = subprocess.Popen(cmd_b1, stderr=subprocess.PIPE, bufsize=0)
            
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
            cmd_a2 = base + ["-ss", "0", "-to", str(split_time), "-i", input_path, "-c:v", "hevc_nvenc", "-preset", "p5", "-b:v", f"{brs[0]}k", "-pass", "2", "-passlogfile", log_a, "-c:a", "copy", p1_path]
            cmd_b2 = base + ["-ss", str(split_time), "-i", input_path, "-c:v", "hevc_nvenc", "-preset", "p5", "-b:v", f"{brs[1]}k", "-pass", "2", "-passlogfile", log_b, "-c:a", "copy", p2_path]

            pa = subprocess.Popen(cmd_a2, stderr=subprocess.PIPE, text=True, bufsize=0)
            pb = subprocess.Popen(cmd_b2, stderr=subprocess.PIPE, text=True, bufsize=0)
            
            t1 = threading.Thread(target=monitor_process, args=(pa, trk, True))
            t2 = threading.Thread(target=monitor_process, args=(pb, trk, False))
            t1.start(); t2.start()
            
            while pa.poll() is None or pb.poll() is None:
                p, f, e = trk.get_stats()
                print(f"\rProg: {p:.1f}% | FPS: {f:.1f} | ETA: {e//3600:02}:{(e%3600)//60:02}:{e%60:02}   ", end="")
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
            print("\nCancelling and cleaning up files...")
            # Kill any running FFmpeg processes
            try:
                pa.kill()
            except Exception:
                pass
            try:
                pb.kill()
            except Exception:
                pass
            return False, "Cancelled by user"
        finally:
            # Cleanup temp directory and all its contents
            try:
                shutil.rmtree(temp_dir)
            except OSError: pass
            clean_log_file()

    # --- SERIAL CPU PATH ---
    else:
        tgt_bits = target_size_mb * MB_TO_BITS
        vid_bits = max(0, tgt_bits - (audio_kbps * 1000 * duration))
        vid_br = f"{math.ceil(vid_bits / duration / 1000)}k"
        
        print(f"CPU Fallback. Target: {vid_br}")
        cmd = [
            ffmpeg_exe, "-y", "-i", input_path, "-c:v", "libx265", "-preset", "medium",
            "-b:v", vid_br, "-maxrate:v", vid_br, "-bufsize:v", f"{int(vid_br[:-1])*2}k",
            "-filter:v", f"fps={fps}", "-tag:v", "hvc1", "-c:a", "copy",
            "-loglevel", "error", "-stats", output_path
        ]
        
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='ignore')
        pat = re.compile(r'time=(\d+:\d+:\d+\.\d+).*?speed=\s*(\d+\.?\d*)x')
        while True:
            line = process.stderr.readline()
            if not line: break
            match = pat.search(line)
            if match:
                h, m, s = map(float, match.group(1).split(':'))
                prog = min(100, ((h*3600 + m*60 + s) / duration) * 100)
                print(f"\rProgress: {prog:.1f}% | Speed: {match.group(2)}x", end="")
        process.wait()
        print()
        if process.returncode != 0: return False, "CPU Encode Failed"

    clean_log_file()
    
    if os.path.exists(output_path):
        final_sz = get_file_size(output_path)
        print(f"Done. Final: {format_size(final_sz)} ({(1-final_sz/orig_bytes)*100:.1f}%)")
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