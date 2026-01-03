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

> [!CAUTION]
> The script currently does not support other GPU vendors besides NVIDIA. It will falback to CPU encoding if no compatible NVIDIA GPU is found.
> AMD (`hevc_amf`/`h264_amf`) and Intel (`hevc_qsv`/`h264_qsv`) GPU acceleration support may be added in future updates.

## How to use
- In Windows:
  - Download any of the target filesize build from [releases](<https://github.com/SimpNick6703/Video-Compression/releases>).
  - Drag and drop your video on the executable. (Or run in Command Prompt/Terminal as `./{size}mb-win64 <input.mp4> [output.mp4]`)

https://github.com/user-attachments/assets/0272427b-0db4-40dd-bd14-37d705d110a0

- In Linux:
  - Download your desired build from [releases](<https://github.com/SimpNick6703/Video-Compression/releases>).
  - Make your downloaded file executable: `chmod +x {size}mb-linux`
  - Run in terminal as `./{size}mb-linux <input.mp4> [output.mp4]`
