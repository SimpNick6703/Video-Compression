import { FFmpeg } from '@ffmpeg/ffmpeg';
import { toBlobURL, fetchFile } from '@ffmpeg/util';

interface VideoMetadata {
    duration: number;
    width: number;
    height: number;
    frameRate: number;
    size: number;
}

interface FFprobeStream {
    r_frame_rate?: string;
    width?: number;
    height?: number;
    duration?: string;
}
interface FFprobeOutput {
    streams?: FFprobeStream[];
}

interface CompressionConfig {
    targetSizeMB: number;
    targetHeight: number | null;
    videoBitrate: number;
    audioBitrate: number;
}

declare const Mp4Muxer: any;
declare const MediaStreamTrackProcessor: any;


class VideoCompressor {
    private videoInput: HTMLInputElement;
    private compressBtn: HTMLButtonElement;
    private downloadBtn: HTMLButtonElement;
    private statusCard: HTMLElement;
    private resultCard: HTMLElement;
    private errorCard: HTMLElement;
    private progressBar: HTMLElement;
    private progressPercent: HTMLElement;
    private statusText: HTMLElement;
    private statsInfo: HTMLElement;
    private resultStats: HTMLElement;
    private errorText: HTMLElement;
    private selectedFile: HTMLElement;
    private targetSizeSelect: HTMLSelectElement;
    private customSizeInput: HTMLInputElement;
    private resolutionSelect: HTMLSelectElement;

    private currentFile: File | null = null;
    private compressedBlob: Blob | null = null;
    private videoMetadata: VideoMetadata | null = null;
    private ffmpeg: FFmpeg | null = null;
    private ffmpegLoaded: boolean = false;

    constructor() {
        this.videoInput = document.getElementById('videoInput') as HTMLInputElement;
        this.compressBtn = document.getElementById('compressBtn') as HTMLButtonElement;
        this.downloadBtn = document.getElementById('downloadBtn') as HTMLButtonElement;
        this.statusCard = document.getElementById('statusCard') as HTMLElement;
        this.resultCard = document.getElementById('resultCard') as HTMLElement;
        this.errorCard = document.getElementById('errorCard') as HTMLElement;
        this.progressBar = document.getElementById('progressBar') as HTMLElement;
        this.progressPercent = document.getElementById('progressPercent') as HTMLElement;
        this.statusText = document.getElementById('statusText') as HTMLElement;
        this.statsInfo = document.getElementById('statsInfo') as HTMLElement;
        this.resultStats = document.getElementById('resultStats') as HTMLElement;
        this.errorText = document.getElementById('errorText') as HTMLElement;
        this.selectedFile = document.getElementById('selectedFile') as HTMLElement;
        this.targetSizeSelect = document.getElementById('targetSize') as HTMLSelectElement;
        this.customSizeInput = document.getElementById('customSize') as HTMLInputElement;
        this.resolutionSelect = document.getElementById('resolution') as HTMLSelectElement;

        this.init();
    }

    private init(): void {
        console.log('[VideoCompressor] Initializing...');
        this.checkCompatibility();
        this.setupEventListeners();
        this.initFFmpeg();
    }

    private async initFFmpeg(): Promise<void> {
        try {
            console.log('[FFmpeg] Initializing...');
            this.ffmpeg = new FFmpeg();
            
            this.ffmpeg.on('log', ({ message }: any) => {
                console.log('[FFmpeg]', message);
            });
            
            const baseURL = 'lib/ffmpeg';
            await this.ffmpeg.load({
                coreURL: await toBlobURL(`${baseURL}/ffmpeg-core.js`, 'text/javascript'),
                wasmURL: await toBlobURL(`${baseURL}/ffmpeg-core.wasm`, 'application/wasm'),
                workerURL: await toBlobURL(`${baseURL}/worker.js`, 'text/javascript'),
            });
            
            this.ffmpegLoaded = true;
            console.log('[FFmpeg] Loaded successfully');
        } catch (error) {
            console.warn('[FFmpeg] Failed to load, will use fallback FPS detection:', error);
            this.ffmpegLoaded = false;
        }
    }

    private checkCompatibility(): void {
        const compatInfo = document.getElementById('compatInfo') as HTMLElement;
        
        const userAgent = navigator.userAgent;
        const browserInfo = this.getBrowserInfo(userAgent);
        console.log(`[Browser] ${browserInfo.name} ${browserInfo.version} on ${navigator.platform}`);
        console.log(`[UserAgent] ${userAgent}`);
        console.log(`[SecureContext] ${window.isSecureContext ? 'YES' : 'NO'} (protocol: ${window.location.protocol}, hostname: ${window.location.hostname})`);
        
        console.log('[Debug] window.VideoEncoder:', typeof (window as any).VideoEncoder);
        console.log('[Debug] window.VideoDecoder:', typeof (window as any).VideoDecoder);
        console.log('[Debug] window.AudioEncoder:', typeof (window as any).AudioEncoder);
        console.log('[Debug] self.VideoEncoder:', typeof (self as any).VideoEncoder);
        
        const hasVideoEncoder = typeof (window as any).VideoEncoder !== 'undefined';
        const hasVideoDecoder = typeof (window as any).VideoDecoder !== 'undefined';
        const hasAudioEncoder = typeof (window as any).AudioEncoder !== 'undefined';
        
        const checks = [
            {
                name: 'Secure Context (HTTPS/localhost)',
                supported: window.isSecureContext,
                critical: true
            },
            {
                name: 'WebCodecs API',
                supported: hasVideoEncoder && hasVideoDecoder,
                critical: true
            },
            {
                name: 'WebCodecs VideoEncoder',
                supported: hasVideoEncoder,
                critical: true
            },
            {
                name: 'WebCodecs VideoDecoder',
                supported: hasVideoDecoder,
                critical: true
            },
            {
                name: 'WebCodecs AudioEncoder',
                supported: hasAudioEncoder,
                critical: false
            },
            {
                name: 'File System Access API',
                supported: 'showSaveFilePicker' in window,
                critical: false
            }
        ];

        let html = `<div class="text-sm mb-2 text-gray-400">Browser: ${browserInfo.name} ${browserInfo.version}</div>`;
        let allCriticalSupported = true;

        checks.forEach(check => {
            const icon = check.supported ? '✓' : '✗';
            const color = check.supported ? 'text-green-400' : 'text-red-400';
            html += `<div class="${color}">${icon} ${check.name}: ${check.supported ? 'Supported' : 'Not Supported'}</div>`;
            
            if (check.critical && !check.supported) {
                allCriticalSupported = false;
            }

            console.log(`[Compatibility] ${check.name}: ${check.supported ? 'YES' : 'NO'}`);
        });

        compatInfo.innerHTML = html;

        if (!allCriticalSupported) {
            if (!window.isSecureContext) {
                const currentUrl = window.location.href;
                const localhostUrl = currentUrl.replace(/https?:\/\/[^\/]+/, 'http://localhost:8000');
                this.showError(
                    `WebCodecs requires a secure context (HTTPS or localhost). ` +
                    `You are accessing via ${window.location.protocol}//${window.location.hostname}. ` +
                    `Please use: http://localhost:8000 or access via HTTPS.`
                );
                console.error('[Compatibility] NOT A SECURE CONTEXT - WebCodecs requires HTTPS or localhost');
                console.error(`[Compatibility] Current: ${currentUrl}`);
                console.error(`[Compatibility] Try: ${localhostUrl}`);
            } else {
                let errorMsg = 'WebCodecs API is not supported in your browser.';
                
                if (browserInfo.name === 'Edge') {
                    const version = parseInt(browserInfo.version || '0');
                    if (version >= 94) {
                        errorMsg += ` Edge ${version} should support WebCodecs. Check edge://settings/privacy for hardware acceleration, or try edge://gpu for WebCodecs status.`;
                    } else {
                        errorMsg += ` Please update to Edge 94+ (you have ${browserInfo.version}).`;
                    }
                } else if (browserInfo.name === 'Chrome' || browserInfo.name === 'Opera') {
                    const minVersion = browserInfo.name === 'Opera' ? 80 : 94;
                    const version = parseInt(browserInfo.version || '0');
                    if (version >= minVersion) {
                        errorMsg += ` Try enabling chrome://flags/#enable-experimental-web-platform-features and restart completely.`;
                    } else {
                        errorMsg += ` Please update to ${browserInfo.name} ${minVersion}+ (you have ${browserInfo.version}).`;
                    }
                } else if (browserInfo.name === 'Firefox' || browserInfo.name === 'Safari') {
                    errorMsg += ` ${browserInfo.name} does not support WebCodecs API. Please use Chrome 94+, Edge 94+, or Opera 80+.`;
                } else {
                    errorMsg += ' Please use Chrome 94+, Edge 94+, or Opera 80+.';
                }
                
                this.showError(errorMsg);
                console.error('[Compatibility] Critical features missing');
            }
            console.error('[Compatibility] Check console debug logs above for details');
        }
    }

    private getBrowserInfo(userAgent: string): { name: string, version: string } {
        let name = 'Unknown';
        let version = '';

        if (userAgent.indexOf('Edg/') > -1) {
            name = 'Edge';
            version = userAgent.match(/Edg\/(\d+)/)?.[1] || '';
        } else if (userAgent.indexOf('OPR/') > -1) {
            name = 'Opera';
            version = userAgent.match(/OPR\/(\d+)/)?.[1] || '';
        } else if (userAgent.indexOf('Chrome/') > -1) {
            name = 'Chrome';
            version = userAgent.match(/Chrome\/(\d+)/)?.[1] || '';
        } else if (userAgent.indexOf('Firefox/') > -1) {
            name = 'Firefox';
            version = userAgent.match(/Firefox\/(\d+)/)?.[1] || '';
        } else if (userAgent.indexOf('Safari/') > -1 && userAgent.indexOf('Chrome') === -1) {
            name = 'Safari';
            version = userAgent.match(/Version\/(\d+)/)?.[1] || '';
        } else if (userAgent.indexOf('Opera/') > -1) {
            name = 'Opera';
            version = userAgent.match(/Version\/(\d+)/)?.[1] || (userAgent.match(/Opera\/(\d+)/)?.[1] || '');
        }

        return { name, version };
    }

    private getAVCLevel(width: number, height: number): string {
        const pixels = width * height;
        
        if (pixels <= 152064) return '001e';
        if (pixels <= 345600) return '001f';
        if (pixels <= 912384) return '0028';
        if (pixels <= 2073600) return '0032';
        if (pixels <= 8294400) return '003c';
        return '0034';
    }

    private setupEventListeners(): void {
        this.videoInput.addEventListener('change', () => this.handleFileSelect());
        this.compressBtn.addEventListener('click', () => this.startCompression());
        this.downloadBtn.addEventListener('click', () => this.downloadFile());
        
        this.targetSizeSelect.addEventListener('change', () => {
            if (this.targetSizeSelect.value === 'custom') {
                this.customSizeInput.classList.remove('hidden');
            } else {
                this.customSizeInput.classList.add('hidden');
            }
        });

        console.log('[VideoCompressor] Event listeners attached');
    }

    private async handleFileSelect(): Promise<void> {
        const files = this.videoInput.files;
        if (!files || files.length === 0) {
            this.currentFile = null;
            this.compressBtn.disabled = true;
            this.selectedFile.textContent = '';
            return;
        }

        this.currentFile = files[0];
        console.log(`[FileSelect] Selected: ${this.currentFile.name} (${this.formatSize(this.currentFile.size)})`);
        
        this.selectedFile.textContent = `${this.currentFile.name} - ${this.formatSize(this.currentFile.size)}`;
        this.hideCards();

        try {
            this.videoMetadata = await this.extractVideoMetadata(this.currentFile);
            console.log('[Metadata]', this.videoMetadata);
            this.compressBtn.disabled = false;
        } catch (error) {
            console.error('[Metadata] Extraction failed:', error);
            this.showError(`Failed to read video metadata: ${error}`);
            this.compressBtn.disabled = true;
        }
    }

    private async extractVideoMetadata(file: File): Promise<VideoMetadata> {
        // Try ffmpeg.wasm first for accurate FPS detection
        if (this.ffmpegLoaded && this.ffmpeg) {
            try {
                return await this.extractVideoMetadataWithFFmpeg(file);
            } catch (error) {
                console.warn('[FFmpeg] Metadata extraction failed, falling back to video element:', error);
            }
        }
        
        // Fallback to video element method
        return this.extractVideoMetadataFallback(file);
    }

    private async extractVideoMetadataWithFFmpeg(file: File): Promise<VideoMetadata> {
        if (!this.ffmpeg) throw new Error('FFmpeg not initialized');
        
        console.log('[FFmpeg] Extracting metadata...');
        const inputFileName = 'input.mp4';
        
        // Write file to ffmpeg virtual filesystem
        await this.ffmpeg.writeFile(inputFileName, await fetchFile(file));
        
        // Use ffmpeg to probe the video file
        // We'll capture stderr output which contains stream info
        let fpsInfo = '';
        let widthInfo = 0;
        let heightInfo = 0;
        let durationInfo = 0;
        
        const logHandler = ({ message }: any) => {
            // FFmpeg logs everything to the same stream in wasm
            // Robust regex for resolution: matches 2560x1440, 1920x1080 etc
            const resMatch = message.match(/Stream.*Video:.*?\s(\d{3,5})x(\d{3,5})/);
            if (resMatch && (!widthInfo || !heightInfo)) {
                widthInfo = parseInt(resMatch[1]);
                heightInfo = parseInt(resMatch[2]);
            }

            // Robust regex for FPS: matches 118.32 fps, 60 fps, etc
            const fpsMatch = message.match(/(\d+\.?\d*)\s*fps/);
            if (fpsMatch && !fpsInfo) {
                fpsInfo = fpsMatch[1];
            }
            
            // Robust regex for Duration: matches Duration: 00:01:10.02
            const durationMatch = message.match(/Duration:\s*(\d+):(\d+):(\d+\.?\d*)/);
            if (durationMatch && !durationInfo) {
                const hours = parseInt(durationMatch[1]);
                const minutes = parseInt(durationMatch[2]);
                const seconds = parseFloat(durationMatch[3]);
                durationInfo = hours * 3600 + minutes * 60 + seconds;
            }
        };

        this.ffmpeg.on('log', logHandler);
        
        try {
            // Use a dummy output to null to force header printing without full processing
            // This is safer than just -i which sometimes aborts differently
            await this.ffmpeg.exec(['-i', inputFileName, '-t', '1', '-f', 'null', '-']);
        } catch (e) {
            console.warn('[FFmpeg] Probe warning (expected):', e);
        }
        
        this.ffmpeg.off('log', logHandler);
        
        // Final fallback to common defaults if extraction failed
        const width = widthInfo || 1920;
        const height = heightInfo || 1080;
        const frameRate = fpsInfo ? Math.round(parseFloat(fpsInfo)) : 30;
        
        console.log(`[FFmpeg] Extraction complete: ${width}x${height}, ${frameRate} fps, ${durationInfo.toFixed(2)}s`);
        
        // Clean up
        await this.ffmpeg.deleteFile(inputFileName);
        
        // Get duration from video element as ultimate fallback
        let duration = durationInfo;
        if (!duration || isNaN(duration)) {
            duration = await this.getVideoDuration(file);
        }
        
        return {
            duration: duration || 0.1, // Never 0
            width: width || 1920,
            height: height || 1080,
            frameRate: frameRate || 30,
            size: file.size
        };
    }

    private async getVideoDuration(file: File): Promise<number> {
        return new Promise((resolve) => {
            const video = document.createElement('video');
            video.preload = 'metadata';
            video.onloadedmetadata = () => {
                const duration = video.duration;
                URL.revokeObjectURL(video.src);
                resolve(duration);
            };
            video.onerror = () => {
                URL.revokeObjectURL(video.src);
                resolve(0);
            };
            video.src = URL.createObjectURL(file);
        });
    }

    private async extractVideoMetadataFallback(file: File): Promise<VideoMetadata> {
        return new Promise(async (resolve, reject) => {
            const video = document.createElement('video');
            video.preload = 'metadata';

            video.onloadedmetadata = async () => {
                let detectedFPS = 30;
                
                try {
                    video.currentTime = 0;
                    await new Promise(r => video.onseeked = r);
                    
                    if ('requestVideoFrameCallback' in video) {
                        let frames = 0;
                        const startTime = performance.now();
                        
                        const countFrame = () => {
                            frames++;
                            if (frames < 60) {
                                (video as any).requestVideoFrameCallback(countFrame);
                            } else {
                                const elapsed = (performance.now() - startTime) / 1000;
                                detectedFPS = Math.round(frames / elapsed);
                                console.log(`[FPS Detection Fallback] Detected: ${detectedFPS} fps`);
                                video.pause();
                                URL.revokeObjectURL(video.src);
                                resolve({
                                    duration: video.duration,
                                    width: video.videoWidth,
                                    height: video.videoHeight,
                                    frameRate: detectedFPS,
                                    size: file.size
                                });
                            }
                        };
                        
                        video.play();
                        (video as any).requestVideoFrameCallback(countFrame);
                        return;
                    }
                } catch (e) {
                    console.warn('[FPS Detection Fallback] Failed, using default 30fps:', e);
                }
                
                URL.revokeObjectURL(video.src);
                resolve({
                    duration: video.duration,
                    width: video.videoWidth,
                    height: video.videoHeight,
                    frameRate: detectedFPS,
                    size: file.size
                });
            };

            video.onerror = () => {
                URL.revokeObjectURL(video.src);
                reject(new Error('Failed to load video metadata'));
            };

            video.src = URL.createObjectURL(file);
        });
    }

    private getCompressionConfig(): CompressionConfig {
        let targetSizeMB: number;
        if (this.targetSizeSelect.value === 'custom') {
            targetSizeMB = parseFloat(this.customSizeInput.value) || 100;
        } else {
            targetSizeMB = parseFloat(this.targetSizeSelect.value);
        }

        let targetHeight: number | null = null;
        if (this.resolutionSelect.value !== 'original') {
            targetHeight = parseInt(this.resolutionSelect.value);
        }

        const targetSizeBytes = targetSizeMB * 1024 * 1024;
        const targetSizeBits = targetSizeBytes * 8;
        const duration = this.videoMetadata!.duration;

        const audioBitrate = 128000;
        const audioBits = audioBitrate * duration;
        const videoBits = targetSizeBits * 0.95 - audioBits;
        const videoBitrate = Math.floor(videoBits / duration);

        console.log(`[Config] Target: ${targetSizeMB}MB, Video bitrate: ${Math.floor(videoBitrate / 1000)}kbps, Audio: 128kbps`);

        return {
            targetSizeMB,
            targetHeight,
            videoBitrate: Math.max(videoBitrate, 100000),
            audioBitrate
        };
    }

    private async startCompression(): Promise<void> {
        if (!this.currentFile || !this.videoMetadata) {
            console.error('[Compression] No file or metadata');
            return;
        }

        this.hideCards();
        this.statusCard.classList.remove('hidden');
        this.compressBtn.disabled = true;

        try {
            const config = this.getCompressionConfig();
            console.log('[Compression] Starting with config:', config);
            
            this.compressedBlob = await this.compressVideo(this.currentFile, config);
            
            console.log(`[Compression] Complete. Output size: ${this.formatSize(this.compressedBlob.size)}`);
            this.showResult(this.compressedBlob);
        } catch (error) {
            console.error('[Compression] Error:', error);
            this.showError(`Compression failed: ${error}`);
        } finally {
            this.compressBtn.disabled = false;
        }
    }

    private async compressVideo(file: File, config: CompressionConfig): Promise<Blob> {
        console.log('[CompressVideo] Decoding input file...');
        this.updateProgress(0, 'Decoding video...');

        const videoElement = document.createElement('video');
        videoElement.src = URL.createObjectURL(file);
        videoElement.muted = true;
        await videoElement.play();
        videoElement.pause();

        let width = this.videoMetadata!.width || 1920;
        let height = this.videoMetadata!.height || 1080;

        console.log(`[CompressVideo] Input resolution: ${width}x${height}`);

        if (config.targetHeight && height > config.targetHeight) {
            const aspectRatio = width / height;
            height = config.targetHeight;
            width = Math.round(height * aspectRatio);
            console.log(`[Resize] Scaling to height ${height}, calculated width ${width}`);
        }

        // Ensure width and height are even and greater than zero
        width = Math.max(2, width - (width % 2));
        height = Math.max(2, height - (height % 2));
        
        console.log(`[CompressVideo] Final resolution: ${width}x${height}`);

        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d')!;

        if (typeof (window as any).Mp4Muxer === 'undefined') {
            throw new Error('Mp4Muxer library not loaded. Ensure lib/mp4-muxer.js is served and script is included before app.js.');
        }

        let muxer: any;
        const MuxerCtor = (window as any).Mp4Muxer?.Muxer || (Mp4Muxer as any)?.Muxer;
        const ArrayBufferTargetCtor = (window as any).Mp4Muxer?.ArrayBufferTarget || (Mp4Muxer as any)?.ArrayBufferTarget;

        muxer = new MuxerCtor({
            target: new ArrayBufferTargetCtor(),
            video: {
                codec: 'avc',
                width: width,
                height: height
            },
            audio: {
                codec: 'aac',
                sampleRate: 48000,
                numberOfChannels: 2
            },
            fastStart: 'in-memory',
            firstTimestampBehavior: 'offset'
        });

        let audioEncoder: AudioEncoder | null = null;
        const audioContext = new AudioContext({ sampleRate: 48000 });
        let audioSource: MediaElementAudioSourceNode | null = null;
        
        try {
            audioSource = audioContext.createMediaElementSource(videoElement);
            const audioDestination = audioContext.createMediaStreamDestination();
            audioSource.connect(audioDestination);
            audioSource.connect(audioContext.destination);

            const audioStream = audioDestination.stream;
            const audioTrack = audioStream.getAudioTracks()[0];
            
            if (audioTrack) {
                console.log('[Audio] Track found, setting up encoder...');
                const audioReader = new MediaStreamTrackProcessor({ track: audioTrack });
                const audioReadable = audioReader.readable;
                
                audioEncoder = new AudioEncoder({
                    output: (chunk, metadata) => {
                        muxer.addAudioChunk(chunk, metadata);
                    },
                    error: (error) => {
                        console.error('[AudioEncoder] Error:', error);
                    }
                });

                audioEncoder.configure({
                    codec: 'mp4a.40.2',
                    sampleRate: 48000,
                    numberOfChannels: 2,
                    bitrate: config.audioBitrate
                });

                const reader = audioReadable.getReader();
                const processAudio = async () => {
                    try {
                        while (true) {
                            const { done, value } = await reader.read();
                            if (done) break;
                            if (audioEncoder && audioEncoder.state === 'configured') {
                                audioEncoder.encode(value);
                            }
                            value.close();
                        }
                    } catch (e) {
                        console.warn('[Audio] Processing stopped:', e);
                    }
                };
                processAudio();
            } else {
                console.log('[Audio] No audio track found in video');
            }
        } catch (e) {
            console.warn('[Audio] Setup failed, proceeding with video only:', e);
        }

        let frameCount = 0;
        const totalFrames = Math.floor(this.videoMetadata!.duration * this.videoMetadata!.frameRate);

        const encoder = new VideoEncoder({
            output: (chunk, metadata) => {
                muxer.addVideoChunk(chunk, metadata);
            },
            error: (error) => {
                console.error('[VideoEncoder] Error:', error);
                throw error;
            }
        });

        const codecLevel = this.getAVCLevel(width, height);
        const encoderConfig: VideoEncoderConfig = {
            codec: `avc1.42${codecLevel}`,
            width,
            height,
            bitrate: config.videoBitrate,
            framerate: this.videoMetadata!.frameRate,
            hardwareAcceleration: 'prefer-hardware',
            avc: { format: 'avc' }
        };

        console.log('[Encoder] Config:', encoderConfig);
        
        try {
            const support = await VideoEncoder.isConfigSupported(encoderConfig);
            if (!support.supported) {
                console.warn('[Encoder] Config not supported, trying fallback...');
                encoderConfig.codec = 'avc1.42003c';
                encoderConfig.hardwareAcceleration = 'no-preference';
            }
            console.log('[Encoder] Final codec:', encoderConfig.codec);
        } catch (e) {
            console.warn('[Encoder] Config check failed:', e);
        }
        
        encoder.configure(encoderConfig);

        const frameDuration = 1000000 / this.videoMetadata!.frameRate;
        videoElement.currentTime = 0;

        for (let i = 0; i < totalFrames; i++) {
            const timestamp = i * frameDuration;
            const currentTime = i / this.videoMetadata!.frameRate;
            
            videoElement.currentTime = currentTime;
            await new Promise(resolve => {
                videoElement.onseeked = resolve;
            });

            ctx.drawImage(videoElement, 0, 0, width, height);
            
            const frame = new VideoFrame(canvas, {
                timestamp,
                duration: frameDuration
            });

            try {
                encoder.encode(frame, { keyFrame: i % 30 === 0 });
            } finally {
                frame.close();
            }

            frameCount++;
            const progress = Math.floor((frameCount / totalFrames) * 100);
            if (frameCount % 10 === 0) {
                this.updateProgress(progress, `Encoding frame ${frameCount}/${totalFrames}`);
                console.log(`[Progress] ${progress}% - Frame ${frameCount}/${totalFrames}`);
            }
        }

        console.log('[Encoder] Flushing...');
        await encoder.flush();
        encoder.close();
        
        if (audioEncoder) {
            console.log('[Audio] Flushing audio encoder...');
            await audioEncoder.flush();
            audioEncoder.close();
        }
        
        if (audioContext) {
            await audioContext.close();
        }
        
        URL.revokeObjectURL(videoElement.src);

        console.log('[Muxing] Finalizing MP4...');
        this.updateProgress(100, 'Finalizing MP4...');
        muxer.finalize();
        
        const outputBuffer = muxer.target.buffer;
        console.log(`[Muxing] Complete. Size: ${this.formatSize(outputBuffer.byteLength)}`);
        
        return new Blob([outputBuffer], { type: 'video/mp4' });
    }

    private updateProgress(percent: number, status: string): void {
        this.progressBar.style.width = `${percent}%`;
        this.progressPercent.textContent = `${percent}%`;
        this.statusText.textContent = `Status: ${status}`;
        
        if (this.videoMetadata) {
            this.statsInfo.innerHTML = `
                <div>Original: ${this.formatSize(this.videoMetadata.size)}</div>
                <div>Duration: ${this.formatTime(this.videoMetadata.duration)}</div>
                <div>Resolution: ${this.videoMetadata.width}x${this.videoMetadata.height}</div>
            `;
        }
    }

    private showResult(blob: Blob): void {
        this.statusCard.classList.add('hidden');
        this.resultCard.classList.remove('hidden');

        const compressionRatio = ((1 - blob.size / this.videoMetadata!.size) * 100).toFixed(1);
        
        this.resultStats.innerHTML = `
            <div class="text-gray-400">Original size: <span class="text-white font-medium">${this.formatSize(this.videoMetadata!.size)}</span></div>
            <div class="text-gray-400">Compressed size: <span class="text-white font-medium">${this.formatSize(blob.size)}</span></div>
            <div class="text-gray-400">Compression ratio: <span class="text-white font-medium">${compressionRatio}%</span></div>
        `;

        console.log(`[Result] Original: ${this.formatSize(this.videoMetadata!.size)}, Compressed: ${this.formatSize(blob.size)}, Ratio: ${compressionRatio}%`);
    }

    private showError(message: string): void {
        this.hideCards();
        this.errorCard.classList.remove('hidden');
        this.errorText.textContent = message;
        console.error(`[Error] ${message}`);
    }

    private hideCards(): void {
        this.statusCard.classList.add('hidden');
        this.resultCard.classList.add('hidden');
        this.errorCard.classList.add('hidden');
    }

    private downloadFile(): void {
        if (!this.compressedBlob || !this.currentFile) return;

        const url = URL.createObjectURL(this.compressedBlob);
        const a = document.createElement('a');
        a.href = url;
        a.download = this.currentFile.name.replace(/\.[^/.]+$/, '_compressed.mp4');
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        console.log(`[Download] File downloaded: ${a.download}`);
    }

    private formatSize(bytes: number): string {
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
    }

    private formatTime(seconds: number): string {
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    }
}

new VideoCompressor();
