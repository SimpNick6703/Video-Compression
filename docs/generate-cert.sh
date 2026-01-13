#!/bin/bash

CERT_DIR="./ssl"
mkdir -p "$CERT_DIR"

echo "Generating self-signed SSL certificate..."

openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout "$CERT_DIR/nginx-selfsigned.key" \
  -out "$CERT_DIR/nginx-selfsigned.crt" \
  -subj "/C=US/ST=State/L=City/O=Organization/OU=Department/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:192.168.31.2"

echo "SSL certificate generated in $CERT_DIR/"
echo "Certificate: nginx-selfsigned.crt"
echo "Key: nginx-selfsigned.key"
