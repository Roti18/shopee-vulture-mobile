FROM python:3.12-slim

LABEL maintainer="shopee-restock-bot"
LABEL description="Shopee ADB Automation Bot"

# Install ADB (android-tools-adb) dan timezone support
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        android-tools-adb \
        tzdata \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Timezone Asia/Jakarta
ENV TZ=Asia/Jakarta
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Python env
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies dulu (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY bot/ bot/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Buat user non-root
RUN groupadd -r botuser && useradd -r -g botuser botuser

# Buat direktori volume, set permission agar bisa ditulis siapapun
RUN mkdir -p /app/data /app/logs /app/screenshots /tmp && \
    chown -R botuser:botuser /app

USER botuser

# Healthcheck: verifikasi file heartbeat yang ditulis bot setiap 5 menit
# Jika file tidak ada atau lebih dari 10 menit tidak diupdate -> unhealthy
HEALTHCHECK --interval=5m --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "\
import time, os; \
f='/tmp/bot_health'; \
assert os.path.exists(f), 'Healthcheck file missing'; \
age=time.time()-os.path.getmtime(f); \
assert age < 600, f'Healthcheck stale: {age:.0f}s'"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "bot.main"]
