# Video-Compression

[![GitHub Release](https://img.shields.io/github/v/release/SimpNick6703/Video-Compression?style=flat-square&color=06b6d4)](https://github.com/SimpNick6703/Video-Compression/releases)
[![GitHub Pre-Release](https://img.shields.io/github/v/release/SimpNick6703/Video-Compression?include_prereleases&label=pre-release&style=flat-square&color=f59e0b)](https://github.com/SimpNick6703/Video-Compression/releases)
[![Downloads](https://img.shields.io/github/downloads/SimpNick6703/Video-Compression/total?style=flat-square&color=8b5cf6)](https://github.com/SimpNick6703/Video-Compression/releases)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg?style=flat-square)](LICENSE)
[![Platform Support](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-emerald?style=flat-square)](#requirements)
[![Architecture Explorer](https://img.shields.io/badge/Architecture-Interactive%20Explorer-cyan?style=flat-square&logo=googlechrome&logoColor=white)](https://simpnick6703.github.io/Video-Compression/)

Discord has strict file size upload limits for free and boosted tiers. Most online video compression tools have arbitrary file upload limits, privacy concerns, or overwhelm you with complex encoding configurations where it is hard to know what settings yield the right file size without ruining video quality.

Most users do not want to configure encoders or calculate bitrates manually—they just want a **target file size** video compression with minimal quality loss.

**Video-Compression** provides zero-config, drag-and-drop standalone executables for Windows, Linux, and macOS. Simply drop your video file onto your chosen target preset to compress it directly for Discord.

https://github.com/user-attachments/assets/a6dfe650-9bcb-4e02-b9d6-36e6b105639e

### Discord Preset Tiers

| Preset Size | Discord Upload Limit & Tier |
|:---|:---|
| `20 MB` | Discord Free-Tier / Server Level 1 Boosts (DMs, Group DMs, and Servers below Level 2) |
| `50 MB` | Discord Server Level 2 Boosts / Basic Nitro Users |
| `100 MB` | Discord Server Level 3 Boosts |
| `1024 MB` | Discord $10 Nitro Users (1 GB limit) |

### Technical Highlights

Under the hood, the engine calculates optimal Bits-Per-Pixel (BPP) rate control, optimizes resolution when needed to preserve clarity, and runs parallel split encoding with full hardware acceleration:

- **NVIDIA GPU**: **NVENC** with `scale_cuda` (decode, scale and encode stay in VRAM)
- **AMD GPU**: **AMD AMF** (`hevc_amf`, `h264_amf`) with `vpp_amf` scaling
- **Intel GPU**: **Intel QSV** (`hevc_qsv`, `h264_qsv`) with `vpp_qsv` scaling (d3d11va decode mapped to QSV via `hwmap`)
- **Linux (AMD/Intel)**: **VA-API** (`hevc_vaapi`, `h264_vaapi`) with `scale_vaapi` scaling
- **macOS (Apple Silicon & Intel)**: **Apple VideoToolbox** (`hevc_videotoolbox`, `h264_videotoolbox`) with `scale_vt` scaling
- **Software Fallback**: Multithreaded CPU encoding (`libx265`, `libx264`)

> [!TIP]
> Visit the [Interactive Architecture Explorer](https://simpnick6703.github.io/Video-Compression/) to inspect the multi-worker parallel pipeline, zero-copy VRAM dataflow, and live Bits-Per-Pixel (BPP) logic calculator.

## Requirements
For running the script locally (your own build):
- Python 3.11+
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
| `--verbose`, `-v` | `flag` | No | `False` | Prints the decision trail live: encoder probes, pipeline tier, split point, bitrates and every FFmpeg command. Setting the environment variable `DEBUG=1` does the same. |

Exit codes: `0` success, `1` failure (including "already smaller than target"), `130` cancelled with Ctrl+C. The preset executables accept the same arguments; the size and codec in their name are defaults that later arguments override.

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

# After `pip install -e .`, the same CLI is also available as a command
videocompress input.mp4 50 h264
```

## Development

### Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev,build]"      # dev: pytest, ruff, psutil; build: pyinstaller
```

| Extra | Installs | Needed for |
|:---|:---|:---|
| *(none)* | `rich` | Running the CLI from source |
| `dev` | `pytest`, `ruff`, `psutil` | Tests, linting, `tools/bench_vitals.py` |
| `build` | `pyinstaller` | `python build.py` |

FFmpeg is looked up in this order: the bundled copy (inside an executable), an `ffmpeg-minimal-builds/` or `minimal-builds/` folder, the repository root, then `PATH`. `python build.py` downloads the minimal build to the repository root.

### Tests and Linting

The tests need no GPU and no FFmpeg; every FFmpeg call is replaced with a fake, and the suite runs in well under a second. Real hardware is covered by running the CLI or the benchmark tool below.

```bash
pytest -q                                   # whole suite
pytest tests/test_hwpipeline.py             # one file
pytest -k fallback                          # tests whose name matches
ruff check .                                # lint (syntax errors, undefined names, unused imports)
ruff check --fix .                          # apply the safe automatic fixes
```

| Test file | Covers |
|:---|:---|
| `test_hwpipeline.py` | Exact FFmpeg arguments for every backend (NVENC, VA-API, AMF, QSV, VideoToolbox) and tier |
| `test_legacy_cmds.py` | The legacy fallback still produces the pre-v1.2 commands |
| `test_tier_fallback.py` | Tier probing order (zero-copy, then upload, then legacy) and the automatic retry |
| `test_concat.py` | Segment stitching with and without `h264_mp4toannexb` |
| `test_diagnostics.py` | In-memory run log and the failure-only error log file |
| `test_version.py` | Version embedding in executables and FFmpeg release selection |

### Building the Executables

```bash
python build.py                  # build all presets into dist/, then clean build/ and *.spec
python build.py --verbose        # same, but keep build/ and *.spec for debugging (-v also works)
python build.py --clean-ffmpeg   # also delete the downloaded ffmpeg/ffprobe afterwards
```

This builds every preset (20, 50, 100 and 1024 MB, each for HEVC and H.264) for the current platform. If `ffmpeg`/`ffprobe` are not already in the repository root, it downloads the newest [ffmpeg-minimal-builds](https://github.com/SimpNick6703/ffmpeg-minimal-builds) release: the highest FFmpeg version, then the highest revision (e.g. `ffmpeg-n9.0.2-r2`). It then checks that the binary contains the hardware filters this platform needs.

| Environment variable | Effect |
|:---|:---|
| `BUILD_VERSION` | Version embedded in the executables and their error logs, and appended to file names (CI passes the release tag, e.g. `v1.2.0`). Defaults to the installed package version, else `dev`. |
| `FFMPEG_RELEASE_TAG` | Pin an exact FFmpeg release (e.g. `ffmpeg-n9.0.2`) instead of the newest one. |
| `FFMPEG_REPO` | Download FFmpeg from a fork (default `SimpNick6703/ffmpeg-minimal-builds`). |
| `GITHUB_TOKEN` / `GH_TOKEN` | Used only to list FFmpeg releases; avoids the low anonymous GitHub API rate limit. |
| `CI` | When set, a missing hardware filter fails the build instead of only warning. |

### Benchmarking

`tools/bench_vitals.py` runs any command and reports wall time, CPU time of the command and everything it starts, host CPU, peak memory and GPU engine load as one JSON line. It is a development tool and is not bundled into the executables.

```bash
python tools/bench_vitals.py --label 4k_v120 --output out.mp4 -- python -m videocompress input-4K60.mp4 out.mp4 100 hevc
python tools/bench_vitals.py --label old --output out.mp4 --csv samples.csv -- ./100mb-hevc-linux-v1.1.6 input-4K60.mp4 out.mp4
```

| Option | Meaning |
|:---|:---|
| `--label` | Name stored in the JSON line (required) |
| `--output` | Encoded file whose size is reported as `output_mb` |
| `--csv` | Also write per-sample rows to this file |
| `--interval` | Sampling interval in seconds (default `0.5`) |
| `--` | Everything after it is the command to measure |

GPU load comes from DRM fdinfo on Linux (Intel and AMD, no root needed) and from `nvidia-smi` on any OS (first NVIDIA GPU). CPU time is exact on Linux and macOS and sampled on Windows (`tree_cpu_source` in the output says which).

### CI/CD Workflows

| Workflow | Runs on | Does |
|:---|:---|:---|
| **CI** (`ci.yml`) | Pull requests, pushes to `main`, manual | `ruff check` and `pytest` on Python 3.11 and 3.13 |
| **Deploy Architecture Page** (`deploy.yml`) | Pushes to `main` that change `docs/`, manual | Publishes `docs/` to GitHub Pages |
| **Build & Release** (`release.yml`) | Creating a GitHub release, manual | Builds the executables on Windows, Linux and macOS; attaches them to the release (manual runs only keep them as run artifacts) |

### Releasing

1. If the release needs a new FFmpeg build, run **Build Minimal FFmpeg** manually from `main` in [ffmpeg-minimal-builds](https://github.com/SimpNick6703/ffmpeg-minimal-builds). Leaving the inputs blank publishes the next revision of the current FFmpeg version (e.g. `ffmpeg-n9.0.2-r2`).
2. Merge the changes here; CI must pass.
3. Create a GitHub release with a version tag (e.g. `v1.2.0-rc1`, marked as a pre-release first). **Build & Release** builds the executables with the newest FFmpeg release and attaches them.

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
> Hardware encodes pick the most GPU-resident pipeline that works on your input, probed automatically on a one-second sample of the real file (no flags needed):
> - **zero-copy**: hardware decode, hardware scale and hardware encode, so frames never return to system memory.
> - **upload**: software decode, then upload, hardware scale and hardware encode. Used when the GPU cannot decode the source format (for example VP9 or H.264 4:2:2 on VA-API).
> - **legacy**: the previous pipeline with CPU scaling. Used when neither of the above works, and as an automatic retry if an encode fails part-way.
>
> The benefit is near-zero CPU use during the encode, which keeps the machine responsive while gaming or streaming. Wall-clock time is not guaranteed to improve, since the GPU encoder is often the limit. The selected tier is shown in the result summary.
> If your dedicated GPU is being bypassed in favor of integrated GPU or CPU encoding, you'll need to manually change the priority logic in the script to suit your hardware setup. Or, you may simply remove unwanted encoders from the priority list in the script, build the executable again, and use that custom build.

## Reporting Problems

If a compression fails, a `<video name>_compress-error.log` file is written next to your input video (or to the system temp folder if that location is not writable). It contains the detected encoders, the chosen pipeline and the exact FFmpeg commands and errors. Please attach it when opening an issue. Successful runs never create this file. Running with `--verbose` (or `-v`) prints the same details live in the terminal.

## Known Issues

- **Brief stutter at the split midpoint (parallel-split hardware encodes):** the two halves are encoded in parallel and concatenated, which leaves a video timestamp gap of roughly 40-100 ms at the midpoint. Playback stutters briefly there, and the video runs slightly behind the audio for the second half. A fix is planned for the next release.

## Privacy Policy

This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it.

Video-Compression runs entirely offline: it reads the input video and writes the compressed video, plus an error log next to the input if a compression fails. The bundled FFmpeg is built with networking disabled. The program is portable: it installs nothing and changes no system settings, and deleting the executable removes it completely.

## License

This project and its prebuilt standalone executables (which bundle static `ffmpeg` and `ffprobe` binaries from [SimpNick6703/ffmpeg-minimal-builds](https://github.com/SimpNick6703/ffmpeg-minimal-builds)) are distributed under the **GNU General Public License v3.0 (GPL-3.0-or-later)**.

See the [LICENSE](LICENSE) file for the full license text. Each release also includes a `THIRD_PARTY_NOTICES-<platform>.txt` file with this license and the licenses of everything bundled in the executables (FFmpeg, x264, x265, Python, PyInstaller and the Python packages used). The notices also list the exact source revisions of the bundled FFmpeg build, whose source is available from the linked upstream repositories and [ffmpeg-minimal-builds](https://github.com/SimpNick6703/ffmpeg-minimal-builds).


