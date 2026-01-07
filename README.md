# Video-Compression
**Discord compliant file size limit video compression:**
Discord has too small file size limit for free. And, any online video compressor as cloud service or standalone installer ones have either file upload limit or too many options to configure and we never know what's the optimal configuration to go with.

A normal user isn't curious about these configuration and just want a _targeted file size_ video compression with minimal quality loss.

So, here's a simple drag and drop usage (In Windows) easy solution.

## Requirements
For running the script locally (your own build):
- Python 3.x
- ffmpeg installed and added to PATH
- CUDA capable GPU for faster encoding (Optional)
- NVENC capable GPU and driver version >= 570.0 for faster encoding (Optional)

For using prebuilt binaries from releases, you just need to download the executable for your platform; no installation required. To make full use of GPU acceleration, you need a compatible NVIDIA GPU.

> [!NOTE]
> You may view if your Nvidia GPU supports NVENC [here](https://developer.nvidia.com/video-encode-decode-support-matrix) and keep your Nvidia GPU driver version 570.0 or higher.

## How to use
- In Windows:
  - Download any of the target filesize build from [releases](<https://github.com/SimpNick6703/Video-Compression/releases>).
  - Drag and drop your video on the executable. (Or run in Command Prompt/Terminal as `./{size}mb-win64 <input.mp4> [output.mp4]`)

https://github.com/user-attachments/assets/0272427b-0db4-40dd-bd14-37d705d110a0

- In Linux:
  - Download your desired build from [releases](<https://github.com/SimpNick6703/Video-Compression/releases>).
  - Make your downloaded file executable: `chmod +x {size}mb-linux`
  - Run in terminal as `./{size}mb-linux <input.mp4> [output.mp4] [target_size_in_mb]`

## How to Build

To build the preset executables yourself:

### Prerequisites
- Python 3.x
- PyInstaller (`pip install pyinstaller`)

### Building
```bash
python build.py
```

This will:
1. Automatically download FFmpeg/FFprobe for your platform (if not already present)
2. Generate all preset executables (8mb, 50mb, 100mb, 500mb) in `dist/`
3. Clean up build artifacts and downloaded binaries

To keep build artifacts for debugging:
```bash
python build.py --verbose
```

## Encoder Priority Logic
Platform | Encoder Priority Chain | Notes
--- | --- | ---
Windows | `hevc_nvenc` -> `hevc_amf` -> `hevc_qsv` -> `libx265` (CPU) | Explicit vendor-specific encoders are required.
Linux | `hevc_nvenc` -> `hevc_vaapi` -> `libx265` (CPU) | `vaapi` covers both AMD and Intel integrated/dedicated.
MacOS | `hevc_videotoolbox` -> `libx265` (CPU only) | VideoToolbox automatically handles AMD, Intel, & Apple Silicon. Older Nvidia GPUs aren't used by Nvidia Video Codec SDK on MacOS and handled by VideoToolbox if supported.

Considering the wide variety of hardware configurations, the script uses the following priority logic to select the best available encoder on majority of users' systems:
> `hevc_nvenc` > `hevc_vaapi` > `hevc_videotoolbox` > `hevc_amf` > `hevc_qsv` > `libx265` (CPU)

> [!NOTE]
> Only NVENC supports Two-Pass encoding among the listed encoders. Other encoders use Single-Pass encoding only.
> If your dedicated GPU is being bypassed in favor of integrated GPU or CPU encoding, you'll need to manually change the priority logic in the script to suit your hardware setup. Or, you may simply remove unwanted encoders from the priority list in the script, build the executable again, and use that custom build.