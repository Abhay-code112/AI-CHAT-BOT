#!/bin/sh
# Writes runtime config so the same image can point at any backend via the API_URL env var.
if [ -n "$API_URL" ]; then
  printf 'window.APP_CONFIG = { API_URL: "%s" };\n' "$API_URL" > /usr/share/nginx/html/config.js
fi
