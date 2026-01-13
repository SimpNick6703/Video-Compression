FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json tsconfig.json ./
RUN npm install && \
    npm install mp4-muxer@4.3.3 @ffmpeg/ffmpeg@0.12.10 @ffmpeg/util@0.12.1 @ffmpeg/core@0.12.6

COPY app.ts ./
RUN npx tsc

# Copy libs to a lib folder
RUN mkdir -p /app/lib/ffmpeg && \
    mkdir -p /app/lib/util && \
    cp node_modules/mp4-muxer/build/mp4-muxer.js /app/lib/ && \
    cp node_modules/@ffmpeg/ffmpeg/dist/esm/*.js /app/lib/ffmpeg/ && \
    cp node_modules/@ffmpeg/core/dist/esm/ffmpeg-core.js /app/lib/ffmpeg/ && \
    cp node_modules/@ffmpeg/core/dist/esm/ffmpeg-core.wasm /app/lib/ffmpeg/ && \
    cp node_modules/@ffmpeg/util/dist/esm/*.js /app/lib/util/

FROM nginx:alpine

COPY --from=builder /app/app.js /usr/share/nginx/html/
COPY --from=builder /app/lib/ /usr/share/nginx/html/lib/
COPY index.html /usr/share/nginx/html/
COPY README.md /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY ssl/ /etc/nginx/ssl/

EXPOSE 80 443

CMD ["nginx", "-g", "daemon off;"]
