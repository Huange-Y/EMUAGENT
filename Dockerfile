# =============================================================================
# Emulation Agent — Multi-stage Docker image
# =============================================================================
# Build:  docker build -t emulation-agent .
# Run:    docker run -d -p 9100:9100 --name emu-agent emulation-agent
# =============================================================================

FROM ubuntu:22.04

LABEL org.opencontainers.image.title="Emulation Agent"
LABEL org.opencontainers.image.description="QEMU-based firmware emulation server for vulnerability research"
LABEL org.opencontainers.image.version="1.0.0"
LABEL org.opencontainers.image.licenses="MIT"

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------------------
# Install system dependencies
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    # QEMU — all common IoT architectures
    qemu-user-static \
    qemu-system-mips \
    qemu-system-arm \
    qemu-system-x86 \
    qemu-system-misc \
    # Firmware extraction tools
    binwalk \
    squashfs-tools \
    cabextract \
    p7zip-full \
    # File analysis
    file \
    binutils \
    # Python runtime
    python3 \
    python3-pip \
    # Utilities used inside emulated environments
    busybox-static \
    # Networking / debugging tools
    tcpdump \
    netcat-openbsd \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Create data directories
# ---------------------------------------------------------------------------
RUN mkdir -p /data/rootfs /data/logs /data/nvram_templates

# ---------------------------------------------------------------------------
# Install Python dependencies
# ---------------------------------------------------------------------------
WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Copy application source
# ---------------------------------------------------------------------------
COPY . .

# ---------------------------------------------------------------------------
# Runtime configuration
# ---------------------------------------------------------------------------
EXPOSE 9100

ENV EMU_ROOTFS_DIR=/data/rootfs
ENV EMU_MAX_TIMEOUT=120
ENV EMU_LOG_LEVEL=INFO

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:9100/api/health || exit 1

CMD ["python3", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9100"]
