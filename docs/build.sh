#!/bin/bash

# Ensure we are in the docs directory
cd "$(dirname "$0")"

echo "Building TypeScript and collecting assets with Docker..."

docker run --rm \
  -v "$(pwd):/app" \
  -w /app \
  node:20-alpine \
  sh -c "npm install && npx tsc && \
    mkdir -p lib/ffmpeg lib/util && \
    cp node_modules/mp4-muxer/build/mp4-muxer.js lib/ && \
    cp node_modules/@ffmpeg/ffmpeg/dist/esm/*.js lib/ffmpeg/ && \
    cp node_modules/@ffmpeg/core/dist/esm/ffmpeg-core.js lib/ffmpeg/ && \
    cp node_modules/@ffmpeg/core/dist/esm/ffmpeg-core.wasm lib/ffmpeg/ && \
    cp node_modules/@ffmpeg/util/dist/esm/*.js lib/util/"

echo "Build complete. app.js and lib/ folder updated for deployment."
