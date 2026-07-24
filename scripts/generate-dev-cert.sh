#!/usr/bin/env sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cert_dir="$repo_dir/certs"
mkdir -p "$cert_dir"
openssl req -x509 -nodes -newkey rsa:3072 -sha256 -days 365 \
  -keyout "$cert_dir/dev.key" -out "$cert_dir/dev.crt" \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
  -addext "keyUsage=digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth"
chmod 600 "$cert_dir/dev.key"
printf 'Development certificate written to %s\n' "$cert_dir"
