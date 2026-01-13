# Video Compressor Web Application

Client-side video compression using WebCodecs API with hardware acceleration.

## Features

- Hardware-accelerated video encoding using WebCodecs API
- Target file size selection (8MB, 50MB, 100MB, 500MB, Custom)
- Resolution scaling options
- Real-time compression progress
- No server upload required - all processing happens in your browser
- Dark theme UI with TailwindCSS

## Browser Compatibility

This application requires a browser with WebCodecs API support:
- Chrome 94+
- Edge 94+
- Opera 80+

Firefox and Safari do not currently support WebCodecs API.

## Development

### Using Docker (Recommended)

Build TypeScript using Docker:
```bash
cd docs
./build.sh
```

Or manually:
```bash
docker run --rm -v "$(pwd):/app" -w /app node:20-alpine sh -c "npm ci && npx tsc"
```

Run the web server with Docker Compose:
```bash
docker-compose up
```

Then open http://localhost:8000 in your browser.

### Without Docker

Install TypeScript:
```bash
npm install
```

Compile the TypeScript file:
```bash
npx tsc
```

Serve locally:
```bash
python -m http.server 8000
```

## Deployment

This folder is configured for GitHub Pages deployment. To deploy:

1. Push this folder to your repository
2. Go to repository Settings > Pages
3. Set Source to "Deploy from a branch"
4. Select the branch containing the `docs` folder
5. Set folder to `/docs`
6. Save

Your site will be available at `https://yourusername.github.io/Video-Compression/`

## Technical Details

- Uses WebCodecs VideoEncoder API for hardware-accelerated H.264 encoding
- Implements frame-by-frame encoding with canvas-based resizing
- Calculates optimal bitrate based on target file size
- Generates MP4 containers with basic muxing
- Comprehensive console logging for debugging

## Limitations

- Audio encoding not yet implemented (video-only output)
- Basic MP4 muxing (may not work in all players)
- Requires modern browser with WebCodecs support
- Processing time depends on video length and browser performance
