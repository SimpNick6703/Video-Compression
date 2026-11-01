# Video-Compression

[![GitHub Release](https://img.shields.io/github/v/release/SimpNick6703/Video-Compression?style=flat-square&color=06b6d4)](https://github.com/SimpNick6703/Video-Compression/releases)
[![Downloads](https://img.shields.io/github/downloads/SimpNick6703/Video-Compression/total?style=flat-square&color=8b5cf6)](https://github.com/SimpNick6703/Video-Compression/releases)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg?style=flat-square)](LICENSE)
[![Platform Support](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-emerald?style=flat-square)](#requirements)
[![Architecture Explorer](https://img.shields.io/badge/Architecture-Interactive%20Explorer-cyan?style=flat-square&logo=googlechrome&logoColor=white)](https://simpnick6703.github.io/Video-Compression/)

**Video-Compression** is a high-performance **Discord video compressor** and **zero-config drag-and-drop FFmpeg wrapper** engineered specifically for deterministic **target file size video compression**. Whether compressing gaming clips, screen recordings, or camera footage to meet Discord upload limits—**20MB**, **50MB**, **100MB**, or **1024MB**—the engine automatically balances optimal Bits-Per-Pixel (BPP) rate control, resolution scaling, and parallel keyframe split encoding.

Powered by hardware acceleration with zero-copy pipelines where available:
- **NVIDIA GPU**: **NVENC scale_cuda** (pure on-chip VRAM resident scaling & encoding)
- **AMD GPU**: **AMD AMF** (`hevc_amf`, `h264_amf`)
- **Intel GPU**: **Intel QSV** (`hevc_qsv`, `h264_qsv`)
- **Linux (AMD/Intel)**: **VA-API** (`hevc_vaapi`, `h264_vaapi`)
- **macOS (Apple Silicon & Intel)**: **Apple VideoToolbox** (`hevc_videotoolbox`, `h264_videotoolbox`)
- **Software Fallback**: Multithreaded CPU encoding (`libx265`, `libx264`)

https://github.com/user-attachments/assets/51b49444-f143-4426-884f-b7e8b4957669

> [!TIP]
> Visit the [Interactive Architecture Explorer](https://simpnick6703.github.io/Video-Compression/) to inspect the multi-worker parallel pipeline, zero-copy VRAM dataflow, and live Bits-Per-Pixel (BPP) logic calculator.

### Discord Preset Tiers

| Preset Size | Discord Upload Limit & Tier |
|:---|:---|
| `20 MB` | Discord Free-Tier / Server Level 1 Boosts (DMs, Group DMs, and Servers below Level 2) |
| `50 MB` | Discord Server Level 2 Boosts / Basic Nitro Users |
| `100 MB` | Discord Server Level 3 Boosts |
| `1024 MB` | Discord $10 Nitro Users (1 GB limit) |

## Requirements
For running the script locally (your own build):
- Python 3.x
- ffmpeg installed and added to PATH
- CUDA capable GPU for faster encoding (Optional)
- HEVC or H.264 encoding capable GPU (Optional)

For using prebuilt binaries from releases, you just need to download the executable for your platform; no installation required (standalone executables bundle lightweight FFmpeg/FFprobe binaries from [ffmpeg-minimal-builds](https://github.com/SimpNick6703/ffmpeg-minimal-builds)). To make full use of GPU acceleration, you need a compatible NVIDIA/AMD/Intel GPU that supports hardware-accelerated video encoding.

> [!NOTE]
> You may view if your Nvidia GPU supports NVENC [here](https://developer.nvidia.com/video-encode-decode-support-matrix) and keep your Nvidia GPU driver version 570.0 or higher.

## How to use
- In Windows:
  - Download any of the target filesize builds from [releases](https://github.com/SimpNick6703/Video-Compression/releases).
  - Drag and drop your video directly onto the executable (e.g., `100mb-hevc-win64.exe`). (Or run from terminal as `./{size}mb-{codec}-win64.exe <input.mp4> [output.mp4]`).



- In Linux:
  - Download your desired build from [releases](<https://github.com/SimpNick6703/Video-Compression/releases>).
  - Make your downloaded file executable: `chmod +x {size}mb-{codec}-linux`
  - Run in terminal as `./{size}mb-{codec}-linux <input.mp4> [output.mp4] [target_size_in_mb]`

- In MacOS:
  - Download your desired build from [releases](<https://github.com/SimpNick6703/Video-Compression/releases>) (prebuilt releases target Apple Silicon `{size}mb-{codec}-macos-arm64`).
  - Make your downloaded file executable: `chmod +x {size}mb-{codec}-macos-arm64`
  - Run in terminal as `./{size}mb-{codec}-macos-arm64 <input.mp4> [output.mp4]`
  - You may need to allow the application from **Settings > Privacy & Security** since MacOS blocks unsigned applications initially unless allowed.
  - *(For Intel Macs)*: Simply run `python build.py` on your machine; it will automatically detect `x86_64`, fetch the matching minimal FFmpeg binary from [ffmpeg-minimal-builds](https://github.com/SimpNick6703/ffmpeg-minimal-builds), and build native `{size}mb-{codec}-macos-x86_64` executables for you.

> [!NOTE]
> `{codec}` can be either `hevc` or `h264` depending on the build you downloaded. HEVC offers better compression efficiency, while H.264 offers better compatibility with older devices.

## How to Run (Python CLI)

You can also run the compression tool directly from source via Python without needing to build standalone executables.

### Syntax
```bash
python videocompress <input.mp4> [output.mp4] [target_size_in_mb] [codec] [--verbose]
# Or using the -m module flag:
python -m videocompress <input.mp4> [output.mp4] [target_size_in_mb] [codec] [--verbose]
```

### Parameters

| Parameter | Type | Required | Default | Description |
|:---|:---|:---|:---|:---|
| `<input.mp4>` | `string` (Path) | **Yes** | — | Path to the source video file. Must exist on disk. |
| `[output.mp4]` | `string` (Path) | No | `<input>_<size>MB.mp4` | Destination path for the compressed video. |
| `[target_size_in_mb]` | `number` | No | `100` | Target file size threshold in megabytes (e.g. `20`, `50`, `100`, `1024`). Maps directly to Discord tiers: `20` MB (Free/Server Lvl 1), `50` MB (Server Lvl 2/Nitro Basic), `100` MB (Server Lvl 3), and `1024` MB ($10 Nitro). |
| `[codec]` | `string` | No | `hevc` | Encoding codec: `hevc` (H.265, higher efficiency) or `h264` (H.264, broader compatibility). |
| `--verbose`, `-v` | `flag` | No | `False` | Enables detailed debug logs and FFmpeg probe output. |

> [!TIP]
> **Invocation and argument order are flexible:**
> * You can invoke the CLI either directly by package directory (`python videocompress ...`) or as a module (`python -m videocompress ...`).
> * Optional arguments can be provided in any order (e.g. `python videocompress input.mp4 50 hevc` or `python videocompress input.mp4 hevc 50`). The CLI automatically detects arguments by type: numeric values as target MB size, `hevc`/`h264` as the codec, existing file paths as the input, and any additional path as the output destination.

### Examples

```bash
# Compress to default 100 MB using HEVC
python videocompress input.mp4

# Compress to 50 MB using HEVC
python videocompress input.mp4 50

# Compress to 20 MB using H.264
python videocompress input.mp4 20 h264

# Custom output destination with 1024 MB limit (module invocation)
python -m videocompress input.mp4 compressed_output.mp4 1024 hevc

# Enable debug logs
python videocompress input.mp4 100 hevc --verbose
```

## How to Build

To build the preset executables yourself:

### Prerequisites
- Python 3.11+
- Dependencies: `pip install -e .` (or `pip install rich pyinstaller`)

### Building
```bash
python build.py
```

This will:
1. Automatically download minimal FFmpeg/FFprobe binaries for your platform from [ffmpeg-minimal-builds](https://github.com/SimpNick6703/ffmpeg-minimal-builds) to the root directory (skipped if already present)
2. Generate all preset executables (20mb, 50mb, 100mb, 1024mb for both HEVC and H.264 codecs) in `dist/`
3. Clean up build artifacts (spec files and build cache)

To keep build artifacts for debugging:
```bash
python build.py --verbose
```

## Encoder Priority Logic
Platform | HEVC Encoder Priority Chain | H.264 Encoder Priority Chain | Notes
--- | --- | --- | ---
Windows | `hevc_nvenc` -> `hevc_amf` -> `hevc_qsv` -> `libx265` | `h264_nvenc` -> `h264_amf` -> `h264_qsv` -> `libx264` | Explicit vendor-specific encoders are required.
Linux | `hevc_nvenc` -> `hevc_vaapi` -> `libx265` | `h264_nvenc` -> `h264_vaapi` -> `libx264` | `vaapi` covers both AMD and Intel integrated/dedicated.
MacOS | `hevc_videotoolbox` -> `libx265` | `h264_videotoolbox` -> `libx264` | VideoToolbox handles AMD, Intel & Apple Silicon. Older Nvidia GPUs use it if supported.

Considering the wide variety of hardware configurations, the script uses a fallback mechanism trying each encoder sequentially to select the best hardware-accelerated encoder available on the user's system before defaulting to software (CPU) encoding:
> `*_nvenc` > `*_vaapi` > `*_videotoolbox` > `*_amf` > `*_qsv` > `libx265` / `libx264` (CPU)

> [!NOTE]
> All hardware encoders (NVENC, AMF, VAAPI, QSV, VideoToolbox) use parallel split single-pass encoding with hardware rate control (including on-chip lookahead and adaptive quantization). Software fallback (libx265 / libx264) uses a contiguous single-pass pipeline.
> If your dedicated GPU is being bypassed in favor of integrated GPU or CPU encoding, you'll need to manually change the priority logic in the script to suit your hardware setup. Or, you may simply remove unwanted encoders from the priority list in the script, build the executable again, and use that custom build.

## License

This project and its prebuilt standalone executables (which bundle static `ffmpeg` and `ffprobe` binaries from [SimpNick6703/ffmpeg-minimal-builds](https://github.com/SimpNick6703/ffmpeg-minimal-builds)) are distributed under the **GNU General Public License v3.0 (GPL-3.0-or-later)**.

See the [LICENSE](LICENSE) file for the full license text.


