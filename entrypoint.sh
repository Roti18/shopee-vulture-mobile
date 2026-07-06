#!/bin/sh
# Fix permission volume mount (owner root:root dari host -> biarkan botuser bisa nulis)
chmod -R 777 /app/data /app/logs /app/screenshots 2>/dev/null || true
# Lanjut ke command utama
exec "$@"
