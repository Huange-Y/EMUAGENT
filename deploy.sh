#!/bin/bash
# =============================================================================
# deploy.sh — One-click deployment for the Emulation Agent
# =============================================================================
#
# Usage:
#   ./deploy.sh [host] [port] [user]
#   ./deploy.sh                     # defaults: your-vm-ip:22 art
#   ./deploy.sh 10.0.0.5 2222 root  # custom host/port/user
#
# Options (pass after positional args):
#   --update     Only update code, do not reinstall system deps or pip pkgs
#   --restart    Restart the existing server (skip code upload)
#   --logs       Tail remote server logs
#   --status     Check remote server health
#
# Environment variables:
#   SSH_KEY      Path to SSH private key (default: ~/.ssh/id_ed25519)
#   AGENT_PORT   Agent server listen port (default: 9100)
#   REMOTE_DIR   Remote installation directory (default: /opt/emulation_agent)
#
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REMOTE_HOST="${1:-your-vm-ip}"
REMOTE_PORT="${2:-22}"
REMOTE_USER="${3:-art}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="${REMOTE_DIR:-/opt/emulation_agent}"
AGENT_PORT="${AGENT_PORT:-9100}"

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }
step()    { echo -e "\n${CYAN}${BOLD}==>${NC} ${BOLD}$*${NC}"; }

# ---------------------------------------------------------------------------
# Parse flags
# ---------------------------------------------------------------------------

MODE="full"          # full | update | restart | logs | status
POSITIONAL_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --update)   MODE="update";   shift ;;
        --restart)  MODE="restart";  shift ;;
        --logs)     MODE="logs";     shift ;;
        --status)   MODE="status";   shift ;;
        *)          POSITIONAL_ARGS+=("$1"); shift ;;
    esac
done

# Re-parse positional args if they were consumed by flags
if [[ ${#POSITIONAL_ARGS[@]} -ge 1 ]]; then
    REMOTE_HOST="${POSITIONAL_ARGS[0]}"
fi
if [[ ${#POSITIONAL_ARGS[@]} -ge 2 ]]; then
    REMOTE_PORT="${POSITIONAL_ARGS[1]}"
fi
if [[ ${#POSITIONAL_ARGS[@]} -ge 3 ]]; then
    REMOTE_USER="${POSITIONAL_ARGS[2]}"
fi

# Build SSH command fragments
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10 -p "${REMOTE_PORT}")
if [[ -f "${SSH_KEY}" ]]; then
    SSH_OPTS+=(-i "${SSH_KEY}")
fi

SSH_CMD=(ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}")
SCP_CMD=(scp "${SSH_OPTS[@]}")
CURL_CMD=(curl -sf --connect-timeout 5 "http://${REMOTE_HOST}:${AGENT_PORT}/api/health")

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

check_local_prereqs() {
    step "Checking local prerequisites"

    # Locate script directory (where the Python source lives)
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    info "Script directory: ${SCRIPT_DIR}"

    local required_files=(
        "server.py"
        "client.py"
        "backend.py"
        "cli.py"
        "requirements.txt"
        "__init__.py"
    )

    local missing=()
    for f in "${required_files[@]}"; do
        if [[ ! -f "${SCRIPT_DIR}/${f}" ]]; then
            missing+=("${f}")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        error "Missing required files in ${SCRIPT_DIR}:"
        for f in "${missing[@]}"; do
            error "  - ${f}"
        done
        exit 1
    fi
    success "All required source files found"
}

check_ssh() {
    step "Checking SSH connectivity to ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PORT}"
    if ${SSH_CMD[@]} "echo 'SSH connection OK'" > /dev/null 2>&1; then
        success "SSH connection established"
    else
        error "Cannot SSH to ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PORT}"
        error "Check that the host is reachable and SSH is configured."
        exit 1
    fi
}

create_remote_dir() {
    step "Creating remote directory: ${REMOTE_DIR}"
    ${SSH_CMD[@]} "sudo mkdir -p ${REMOTE_DIR} && sudo chown -R ${REMOTE_USER}:${REMOTE_USER} ${REMOTE_DIR}"
    success "Remote directory ready"
}

upload_files() {
    step "Uploading Python source files to ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"
    local files=(
        "server.py"
        "client.py"
        "backend.py"
        "cli.py"
        "requirements.txt"
        "__init__.py"
    )
    for f in "${files[@]}"; do
        local local_path="${SCRIPT_DIR}/${f}"
        if [[ -f "${local_path}" ]]; then
            info "  Uploading ${f} ..."
            ${SCP_CMD[@]} "${local_path}" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"
        else
            warn "  Skipping ${f} (not found locally)"
        fi
    done
    success "Files uploaded"
}

install_system_deps() {
    step "Installing system dependencies on remote host"
    ${SSH_CMD[@]} <<'REMOTE_SCRIPT'
set -euo pipefail

echo "[INFO] Updating package lists..."
sudo apt-get update -qq

echo "[INFO] Installing QEMU packages..."
sudo apt-get install -y -qq --no-install-recommends \
    qemu-user-static \
    qemu-system-mips \
    qemu-system-arm \
    qemu-system-x86 \
    qemu-system-misc \
    binwalk \
    squashfs-tools \
    cabextract \
    p7zip-full \
    file \
    binutils \
    python3 \
    python3-pip \
    busybox-static \
    tcpdump \
    netcat-openbsd \
    curl

echo "[INFO] Verifying QEMU binaries..."
for qemu_bin in qemu-mipsel-static qemu-mips-static qemu-arm-static qemu-aarch64-static; do
    if command -v "${qemu_bin}" &>/dev/null; then
        echo "  [OK]  ${qemu_bin} found"
    else
        echo "  [WARN] ${qemu_bin} NOT found (may be needed for some architectures)"
    fi
done

echo "[INFO] Verifying tar..."
if command -v tar &>/dev/null; then
    echo "  [OK]  tar found"
else
    echo "  [FAIL] tar NOT found — installing..."
    sudo apt-get install -y -qq tar
fi

echo "[OK] System dependencies installed"
REMOTE_SCRIPT
    success "System dependencies installed"
}

install_python_deps() {
    step "Installing Python dependencies on remote host"
    ${SSH_CMD[@]} "cd ${REMOTE_DIR} && sudo pip3 install --no-cache-dir -r requirements.txt"
    success "Python dependencies installed"
}

kill_existing_server() {
    step "Stopping any existing emulation agent server on remote host"
    ${SSH_CMD[@]} <<REMOTE_SCRIPT
# Kill any uvicorn process serving server:app
pids=\$(pgrep -f "uvicorn.*server:app" 2>/dev/null || true)
if [[ -n "\${pids}" ]]; then
    echo "[INFO] Found running server process(es): \${pids}"
    kill \${pids} 2>/dev/null || true
    sleep 2
    # Force kill if still running
    pids=\$(pgrep -f "uvicorn.*server:app" 2>/dev/null || true)
    if [[ -n "\${pids}" ]]; then
        echo "[INFO] Force-killing: \${pids}"
        kill -9 \${pids} 2>/dev/null || true
    fi
    echo "[OK] Server stopped"
else
    echo "[INFO] No existing server process found"
fi

# Also free the agent port if something else is using it
if command -v fuser &>/dev/null; then
    fuser -k ${AGENT_PORT}/tcp 2>/dev/null || true
fi
REMOTE_SCRIPT
    success "Existing server stopped (if any)"
}

start_server() {
    step "Starting emulation agent server on remote host (port ${AGENT_PORT})"
    ${SSH_CMD[@]} <<REMOTE_SCRIPT
cd ${REMOTE_DIR}

# Ensure data directories exist
mkdir -p /data/rootfs /data/logs /data/nvram_templates

nohup python3 -m uvicorn server:app \
    --host 0.0.0.0 \
    --port ${AGENT_PORT} \
    --log-level info \
    > /data/logs/emulation_agent.log 2>&1 &

echo "[OK] Server PID: \$!"
sleep 3

# Verify it is listening
if pgrep -f "uvicorn.*server:app" > /dev/null; then
    echo "[OK] Server process is running"
else
    echo "[FAIL] Server process not found after start"
fi
REMOTE_SCRIPT
    success "Server started on ${REMOTE_HOST}:${AGENT_PORT}"
}

verify_server() {
    step "Verifying server health endpoint"
    local max_attempts=10
    local attempt=0
    while [[ ${attempt} -lt ${max_attempts} ]]; do
        if ${CURL_CMD[@]} 2>/dev/null; then
            echo ""
            success "Server is healthy and responding at http://${REMOTE_HOST}:${AGENT_PORT}/api/health"
            return 0
        fi
        attempt=$((attempt + 1))
        if [[ ${attempt} -lt ${max_attempts} ]]; then
            info "  Waiting for server to be ready... (attempt ${attempt}/${max_attempts})"
            sleep 2
        fi
    done
    error "Server did not respond to health check after ${max_attempts} attempts"
    error "Check remote logs: ${REMOTE_DIR}/logs/emulation_agent.log"
    return 1
}

# ---------------------------------------------------------------------------
# Mode: show logs
# ---------------------------------------------------------------------------

show_logs() {
    step "Tailing remote server logs (Ctrl+C to stop)"
    ${SSH_CMD[@]} "tail -n 100 /data/logs/emulation_agent.log 2>/dev/null || echo '(no logs yet)'"
    echo ""
    info "Live tail:"
    ${SSH_CMD[@]} "tail -f /data/logs/emulation_agent.log 2>/dev/null || echo '(cannot tail)'"
}

# ---------------------------------------------------------------------------
# Mode: show status
# ---------------------------------------------------------------------------

show_status() {
    step "Checking server status"
    if ${CURL_CMD[@]} 2>/dev/null; then
        echo ""
        success "Server is healthy — http://${REMOTE_HOST}:${AGENT_PORT}/api/health"
    else
        warn "Server is NOT reachable at http://${REMOTE_HOST}:${AGENT_PORT}/api/health"
        info "Checking if process is running on remote host..."
        ${SSH_CMD[@]} "pgrep -af 'uvicorn.*server:app' || echo '(no process found)'"
    fi
}

# ---------------------------------------------------------------------------
# Full deploy
# ---------------------------------------------------------------------------

do_full_deploy() {
    echo -e "${BOLD}${CYAN}"
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║         Emulation Agent — Full Deployment               ║"
    echo "║         ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PORT}  →  ${REMOTE_DIR}"
    echo "╚══════════════════════════════════════════════════════════╝"
    echo -e "${NC}"

    check_local_prereqs
    check_ssh
    create_remote_dir
    upload_files
    install_system_deps
    install_python_deps
    kill_existing_server
    start_server
    verify_server

    echo ""
    echo -e "${GREEN}${BOLD}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}${BOLD}  Deployment complete!${NC}"
    echo -e "${GREEN}  Agent URL: http://${REMOTE_HOST}:${AGENT_PORT}${NC}"
    echo -e "${GREEN}  Health:    curl http://${REMOTE_HOST}:${AGENT_PORT}/api/health${NC}"
    echo -e "${GREEN}  Logs:      ssh ${REMOTE_USER}@${REMOTE_HOST} tail -f /data/logs/emulation_agent.log${NC}"
    echo -e "${GREEN}${BOLD}═══════════════════════════════════════════════════════════${NC}"
}

# ---------------------------------------------------------------------------
# Mode dispatcher
# ---------------------------------------------------------------------------

case "${MODE}" in
    full)
        do_full_deploy
        ;;
    update)
        check_ssh
        create_remote_dir
        upload_files
        kill_existing_server
        start_server
        verify_server
        success "Update complete — server restarted with new code"
        ;;
    restart)
        check_ssh
        kill_existing_server
        start_server
        verify_server
        success "Server restarted"
        ;;
    logs)
        check_ssh
        show_logs
        ;;
    status)
        show_status
        ;;
    *)
        error "Unknown mode: ${MODE}"
        exit 1
        ;;
esac
