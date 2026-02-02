FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    supervisor \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install tdl (Telegram Downloader)
# Note: This is a placeholder. You need to install tdl based on your platform.
# For example, download from https://github.com/iyear/tdl/releases
RUN wget -O /tmp/tdl.tar.gz https://github.com/iyear/tdl/releases/download/v0.17.1/tdl_Linux_64bit.tar.gz \
    && tar -xzf /tmp/tdl.tar.gz -C /usr/local/bin/ \
    && chmod +x /usr/local/bin/tdl \
    && rm /tmp/tdl.tar.gz

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Copy supervisord configuration
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Create necessary directories
RUN mkdir -p /data/logs /data/tdl_session /data/files /data/notes

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Expose volumes
VOLUME ["/data"]

# Run supervisord
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
