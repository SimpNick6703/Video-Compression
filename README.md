# Video-Compression
**Discord compliant file size limit video compression:**
Discord has too small file size limit for free. And, any online video compressor as cloud service or standalone installer ones have either file upload limit or too many options to configure and we never know what's the optimal configuration to go with.

A normal user isn't curious about these configuration and just want a _targeted file size_ video compression with minimal quality loss.

So, here's a simple drag and drop usage (In Windows) easy solution.
> [!TIP]
> Visit [this page](https://simpnick6703.github.io/Video-Compression) for Architectural overview and more.

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
  - Download any of the target filesize build from [releases](<https://github.com/SimpNick6703/Video-Compression/releases>).
  - Drag and drop your video on the executable. (Or run in Command Prompt/Terminal as `./{size}mb-{codec}-win64.exe <input.mp4> [output.mp4]`)



https://github.com/user-attachments/assets/51b49444-f143-4426-884f-b7e8b4957669



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
python -m videocompress <input.mp4> [output.mp4] [target_size_in_mb] [codec] [--verbose]
```

### Parameters

| Parameter | Type | Required | Default | Description |
|:---|:---|:---|:---|:---|
| `<input.mp4>` | `string` (Path) | **Yes** | — | Path to the source video file. Must exist on disk. |
| `[output.mp4]` | `string` (Path) | No | `<input>_<size>mb_<codec>.mp4` | Destination path for the compressed video. |
| `[target_size_in_mb]` | `integer` | No | `100` | Target file size threshold in megabytes (e.g. `20`, `50`, `100`, `500`). |
| `[codec]` | `string` | No | `hevc` | Encoding codec: `hevc` (H.265, higher efficiency) or `h264` (H.264, broader compatibility). |
| `--verbose`, `-v` | `flag` | No | `False` | Enables detailed debug logs and FFmpeg probe output. |

> [!TIP]
> **Argument order is flexible:** Optional arguments can be provided in any order (e.g. `python -m videocompress input.mp4 50 hevc` or `python -m videocompress input.mp4 hevc 50`). The CLI automatically detects arguments by type: numeric values as target MB size, `hevc`/`h264` as the codec, existing file paths as the input, and any additional path as the output destination.

### Examples

```bash
# Compress to default 100 MB using HEVC
python -m videocompress input.mp4

# Compress to 50 MB using HEVC
python -m videocompress input.mp4 50

# Compress to 20 MB using H.264
python -m videocompress input.mp4 20 h264

# Custom output destination with 500 MB limit
python -m videocompress input.mp4 compressed_output.mp4 500 hevc

# Enable debug logs
python -m videocompress input.mp4 100 hevc --verbose
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
1. Automatically download minimal FFmpeg/FFprobe binaries for your platform from [ffmpeg-minimal-builds](https://github.com/SimpNick6703/ffmpeg-minimal-builds) (if not already present)
2. Generate all preset executables (20mb, 50mb, 100mb, 500mb for both HEVC and H.264 codecs) in `dist/`
3. Clean up build artifacts and downloaded binaries

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
> NVENC and AMF (Windows only) support Two-Pass encoding among the listed encoders. Other encoders use Single-Pass encoding only. This is due to hardware driver support for specific encoder commands, not a limitation of this script's code.
> If your dedicated GPU is being bypassed in favor of integrated GPU or CPU encoding, you'll need to manually change the priority logic in the script to suit your hardware setup. Or, you may simply remove unwanted encoders from the priority list in the script, build the executable again, and use that custom build.
