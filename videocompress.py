import sys
import subprocess
import time
import os
import re
import math
from pathlib import Path
from typing import Tuple, Optional, Union, List

# --- Constants ---
MB_TO_BYTES = 1024 * 1024
MB_TO_BITS = 8 * 1024 * 1024
LOG_FILES_TO_CLEAN = ["ffmpeg2pass.log", "ffmpeg2pass-0.log", "ffmpeg2pass-0.log.mbtree"] 

# --- Utility Functions ---

def get_file_size(file_path: str) -> int:
    return os.path.getsize(file_path)

def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < MB_TO_BYTES:
        return f"{size_bytes/1024:.2f} KB"
    else:
        return f"{size_bytes/MB_TO_BYTES:.2f} MB"

def clean_log_file():
    """Removes the temporary FFmpeg log files."""
    for log_file in LOG_FILES_TO_CLEAN:
        try:
            if os.path.exists(log_file):
                os.remove(log_file)
        except Exception:
            pass

def check_nvenc_available() -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, check=True, timeout=5
        ).check_returncode()
        return "h264_nvenc" in subprocess.getoutput("ffmpeg -encoders")
    except Exception:
        return False

def get_video_info(input_path: str) -> Optional[Tuple[float, int, float, int]]:
    try:
        cmd_fps = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1", input_path]
        fps_output = subprocess.check_output(cmd_fps, text=True).strip()
        
        cmd_audio_br = ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=bit_rate", "-of", "default=noprint_wrappers=1:nokey=1", input_path]
        audio_bitrate_output = subprocess.check_output(cmd_audio_br, text=True).strip()
        
        cmd_duration = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_path]
        duration_output = subprocess.check_output(cmd_duration, text=True).strip()
        
        if not fps_output or '/' not in fps_output:
            print("Error: Could not determine video FPS.")
            return None
        num, den = map(int, fps_output.split('/'))
        fps = num / den
        
        duration = float(duration_output)

        audio_bitrate_bps = int(audio_bitrate_output) if audio_bitrate_output.isdigit() else 192000
        audio_bitrate_kbps = math.ceil(audio_bitrate_bps / 1000)
        
        original_size_bytes = get_file_size(input_path)
        
        return duration, original_size_bytes, fps, audio_bitrate_kbps
    
    except Exception as e:
        print(f"Error during video info extraction: {e}")
        return None

def run_ffmpeg_pass(cmd: List[str], duration: Optional[float] = None) -> Tuple[bool, str]:
    print("\r", end="")

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='ignore'
        )
    except FileNotFoundError:
        return False, "Error: FFmpeg or ffprobe executable not found. Ensure they are in your PATH or accessible to the executable."
    except Exception as e:
        return False, f"Error starting FFmpeg process: {str(e)}"

    if process.stderr is None:
        process.wait()
        return False, "Error: Failed to open FFmpeg stderr stream for progress reading."

    stderr_lines = []
    fps_pattern = re.compile(r'fps=\s*(\d+\.?\d*)')
    bitrate_pattern = re.compile(r'bitrate=\s*(\d+\.\d+|\d+)(?:kbits/s|Mbits/s|bits/s)?')
    
    for line in iter(process.stderr.readline, ''):
        stderr_lines.append(line)
        
        if line.lower().startswith('error'):
             print(line, end="")

        progress_info = []
        current_time = 0.0
        
        # 1. Calculate Progress %
        if duration and "time=" in line:
            time_str = line.split("time=")[1].split()[0]
            try:
                parts = list(map(float, time_str.split(':')))
                if len(parts) == 3:
                    hrs, mins, secs = parts
                elif len(parts) == 2:
                    hrs, mins, secs = 0.0, parts[0], parts[1]
                else:
                    hrs, mins, secs = 0.0, 0.0, parts[0]

                current_time = hrs * 3600 + mins * 60 + secs
                progress = min(100, current_time / duration * 100)
                progress_info.append(f"Progress: {progress:.1f}%")
            except Exception:
                pass
        
        # 2. Get Current Speed (FPS and Multiplier)
        current_fps = 0.0
        fps_match = fps_pattern.search(line)
        if fps_match:
            current_fps = float(fps_match.group(1))
            progress_info.append(f"Speed: {current_fps:.1f} FPS")
        
        # 3. Calculate Estimated Time Remaining (ETA) for Pass 2
        is_pass_two = ("-pass" in cmd and "2" in cmd)
        
        if duration and current_time > 0 and is_pass_two:
            speed_match = re.search(r'speed=\s*(\d+\.?\d*)x', line)
            if speed_match:
                speed_multiplier = float(speed_match.group(1))
                if speed_multiplier > 0:
                    time_remaining_seconds = (duration - current_time) / speed_multiplier
                    
                    if time_remaining_seconds > 0:
                        hours = int(time_remaining_seconds // 3600)
                        minutes = int((time_remaining_seconds % 3600) // 60)
                        seconds = int(time_remaining_seconds % 60)
                        
                        time_str = f"{hours}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes:02d}:{seconds:02d}"
                        progress_info.append(f"ETA: {time_str}")

        
        # 4. Get Bitrate
        bitrate_match = bitrate_pattern.search(line)
        if bitrate_match:
            bitrate = bitrate_match.group(1)
            progress_info.append(f"Bitrate: {bitrate} kbits/s")
        
        if progress_info:
            print(f"\r{' | '.join(progress_info)}", end="")

    process.wait()

    if process.returncode != 0:
        return False, "".join(stderr_lines)
    
    print()
    return True, ""


# --- Main Compression Function ---

def compress_video(input_path: str, output_path: Optional[str] = None, target_size_mb: int = 100) -> Tuple[bool, str]:
    start_time = time.time()
    success = False
    result_msg = ""
    
    # 1. Check/Cleanup before running
    clean_log_file() 
    
    if not os.path.exists(input_path):
        return False, f"Error: Input file '{input_path}' not found"
    
    video_info = get_video_info(input_path)
    if not video_info:
        return False, "Failed to get necessary video information."
    
    duration, original_size_bytes, fps, audio_bitrate_kbps = video_info
    original_size_mb = original_size_bytes / MB_TO_BYTES
    
    if original_size_mb <= target_size_mb:
        clean_log_file()
        return False, f"Video is already smaller than the target size: {original_size_mb:.2f} MB."
    
    # Calculate required VIDEO-ONLY bitrate
    audio_size_bits = audio_bitrate_kbps * 1000 * duration
    target_size_bits = target_size_mb * MB_TO_BITS
    
    target_video_bits = max(0, target_size_bits - audio_size_bits)
    target_video_bitrate_bps = target_video_bits / duration
    
    target_video_bitrate_kbps = math.ceil(target_video_bitrate_bps / 1000)
    target_video_bitrate_ffmpeg = f"{target_video_bitrate_kbps}k"
    
    print(f"Targeting: {target_size_mb} MB | Video Bitrate: {target_video_bitrate_kbps} kbps")

    if output_path is None:
        output_path = str(Path(input_path).with_name(f"{Path(input_path).stem}_{target_size_mb}MB{Path(input_path).suffix}"))

    use_nvenc = check_nvenc_available()
    vcodec = 'h264_nvenc' if use_nvenc else 'libx264'
    preset = 'p5' if use_nvenc else 'medium'
    
    base_config_args = [
        "-i", input_path, 
        "-c:v", vcodec, "-preset", preset,
        "-b:v", target_video_bitrate_ffmpeg,
        "-maxrate:v", target_video_bitrate_ffmpeg,
        "-bufsize:v", f"{target_video_bitrate_kbps * 2}k",
        "-filter:v", f"fps={fps}", 
        "-tag:v", "avc1", 
        "-c:a", "copy"
    ]
    
    try:
        if use_nvenc:
            print("Using NVENC (Two-Pass)...")
            
            base_config_args.extend(["-rc", "vbr", "-spatial-aq", "1", "-temporal-aq", "1", "-aq", "1"])
            
            # --- Pass 1: Analysis (With Progress Parsing) ---
            print("Pass 1/2: Analyzing...")
            pass1_cmd = [
                "ffmpeg", *base_config_args, 
                "-pass", "1", 
                "-loglevel", "error", 
                "-stats",
                "-f", "null", 
                "-an",
                "-y",
                "NUL" if os.name == 'nt' else "/dev/null"
            ]
            
            success, error_msg = run_ffmpeg_pass(pass1_cmd, duration)
            if not success:
                return False, f"FFmpeg Pass 1 failed. Error:\n{error_msg}"

            # --- Pass 2: Encoding (With Progress Parsing) ---
            print("Pass 2/2: Encoding...")
            pass2_cmd = [
                "ffmpeg", *base_config_args,
                "-pass", "2",
                "-loglevel", "error", # suppress header, keep errors
                "-stats", # prints progress to stderr
                "-y", 
                output_path
            ]
            
            success, error_msg = run_ffmpeg_pass(pass2_cmd, duration)
            if not success:
                return False, f"FFmpeg Pass 2 failed. Error:\n{error_msg}"

        else:
            print("Using libx264 CPU (One-Pass ABR)...")
            
            base_config_args.extend([
                "-crf", "23", 
                "-x264-params", "aq-mode=3:no-sao=1"
            ])

            # --- Single Pass Encoding (With Progress Parsing) ---
            single_pass_cmd = [
                "ffmpeg", *base_config_args,
                "-y", 
                "-loglevel", "error", 
                "-stats",
                output_path
            ]
            
            success, error_msg = run_ffmpeg_pass(single_pass_cmd, duration)
            if not success:
                return False, f"FFmpeg Encoding failed. Error:\n{error_msg}"
        
        result_msg = output_path

    finally:
        # Final cleanup regardless of success/failure
        clean_log_file()

    if success:
        print("Processing complete!")
        
        if os.path.exists(output_path):
            compressed_size = get_file_size(output_path)
            reduction = (1 - compressed_size / original_size_bytes) * 100
            
            print(f"Final size: {format_size(compressed_size)}")
            print(f"Reduction: {reduction:.1f}%")
        
        elapsed_time = time.time() - start_time
        print(f"Execution time: {elapsed_time:.2f} seconds")
        
        return True, result_msg
    else:
        return False, result_msg

# --- Main Execution Block ---

if __name__ == "__main__":
    
    # Allow up to 4 arguments (script name, input, output, size)
    if len(sys.argv) < 2 or len(sys.argv) > 4:
        print("Usage: python script.py <input_file> [output_file] [target_size_mb]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = None
    TARGET_COMPRESSION_SIZE_MB = 100 # Default hardcoded size
    
    # Handle Output File (2nd argument)
    if len(sys.argv) >= 3:
        output_file = sys.argv[2]

    # Handle Target Size (3rd argument)
    if len(sys.argv) == 4:
        if sys.argv[3].isdigit():
            TARGET_COMPRESSION_SIZE_MB = int(sys.argv[3])
        else:
            print(f"Error: Target size '{sys.argv[3]}' is not a valid integer.")
            sys.exit(1)

    success, result = compress_video(input_file, output_file, target_size_mb=TARGET_COMPRESSION_SIZE_MB)
    
    if success:
        print(f"Conversion successful: {result}")
    else:
        print(f"Conversion failed: {result}")
        if "already smaller" not in result:
             sys.exit(1)