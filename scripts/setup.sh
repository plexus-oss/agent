#!/bin/bash
#
# Plexus Agent Setup Script
#
# Usage:
#   curl -sL https://app.plexus.company/setup | bash
#   curl -sL https://app.plexus.company/setup | bash -s -- --code ABC123
#
# This script:
#   1. Installs the Plexus agent
#   2. Pairs the device with your account
#   3. Sets up auto-start on boot (systemd)
#   4. Starts the agent
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Parse arguments
PAIRING_CODE=""
DEVICE_NAME=""
SKIP_SERVICE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --code|-c)
            PAIRING_CODE="$2"
            shift 2
            ;;
        --name|-n)
            DEVICE_NAME="$2"
            shift 2
            ;;
        --no-service)
            SKIP_SERVICE=true
            shift
            ;;
        *)
            shift
            ;;
    esac
done

echo ""
echo "┌─────────────────────────────────────────┐"
echo "│  Plexus Agent Setup                     │"
echo "└─────────────────────────────────────────┘"
echo ""

# Detect OS and architecture
OS=$(uname -s)
ARCH=$(uname -m)

echo -e "  System:  ${CYAN}$OS $ARCH${NC}"

# Check for Python
if command -v python3 &> /dev/null; then
    PYTHON=python3
elif command -v python &> /dev/null; then
    PYTHON=python
else
    echo -e "  ${RED}Error: Python not found${NC}"
    echo ""
    echo "  Please install Python 3.8+ first:"
    echo "    sudo apt install python3 python3-pip"
    echo ""
    exit 1
fi

PYTHON_VERSION=$($PYTHON --version 2>&1 | cut -d' ' -f2)
echo -e "  Python:  ${CYAN}$PYTHON_VERSION${NC}"

# Check if python3-venv is available (needed for virtual environments on Debian)
if [ "$OS" = "Linux" ] && ! $PYTHON -c "import venv" 2>/dev/null; then
    echo ""
    echo -e "  ${YELLOW}Installing python3-venv...${NC}"
    if [ "$EUID" -eq 0 ]; then
        apt-get update -qq && apt-get install -y -qq python3-venv
    elif sudo -n true 2>/dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv
    else
        echo -e "  ${RED}Error: python3-venv is required but not installed${NC}"
        echo ""
        echo "  Please run: sudo apt install python3-venv"
        echo ""
        exit 1
    fi
fi

echo ""

# Step 1: Install plexus-agent
echo "─────────────────────────────────────────"
echo ""
echo "  Installing Plexus agent..."
echo ""

# Use a virtual environment to avoid PEP 668 issues on modern Debian/Ubuntu
VENV_DIR="/opt/plexus/venv"
PLEXUS_BIN_DIR="/opt/plexus/bin"

# Create directories (may need sudo on Linux)
if [ "$OS" = "Linux" ]; then
    if [ "$EUID" -eq 0 ]; then
        mkdir -p /opt/plexus
    elif sudo -n true 2>/dev/null; then
        sudo mkdir -p /opt/plexus
        sudo chown $USER:$USER /opt/plexus
    else
        # Fall back to user directory if no sudo
        VENV_DIR="$HOME/.plexus/venv"
        PLEXUS_BIN_DIR="$HOME/.plexus/bin"
        mkdir -p "$HOME/.plexus"
    fi
fi

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "  Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR"
fi

# Activate venv and install
VENV_PIP="$VENV_DIR/bin/pip"

# Install with sensor support by default on Linux (likely Raspberry Pi)
if [ "$OS" = "Linux" ]; then
    "$VENV_PIP" install --upgrade pip --quiet
    "$VENV_PIP" install --upgrade plexus-agent[sensors] --quiet 2>/dev/null || \
    "$VENV_PIP" install --upgrade plexus-agent --quiet
else
    # macOS/other - use system pip or venv
    if [ -f "$VENV_PIP" ]; then
        "$VENV_PIP" install --upgrade pip --quiet
        "$VENV_PIP" install --upgrade plexus-agent --quiet
    else
        # Fall back to system pip on macOS
        pip3 install --upgrade plexus-agent --quiet 2>/dev/null || \
        $PYTHON -m pip install --upgrade plexus-agent --quiet
    fi
fi

# Create symlink so 'plexus' command is available system-wide
VENV_PLEXUS="$VENV_DIR/bin/plexus"
if [ -f "$VENV_PLEXUS" ]; then
    mkdir -p "$PLEXUS_BIN_DIR"
    ln -sf "$VENV_PLEXUS" "$PLEXUS_BIN_DIR/plexus"

    # Add to PATH via profile if not already there
    if [ "$OS" = "Linux" ]; then
        PROFILE_FILE="$HOME/.bashrc"
        if ! grep -q "$PLEXUS_BIN_DIR" "$PROFILE_FILE" 2>/dev/null; then
            echo "" >> "$PROFILE_FILE"
            echo "# Plexus agent" >> "$PROFILE_FILE"
            echo "export PATH=\"$PLEXUS_BIN_DIR:\$PATH\"" >> "$PROFILE_FILE"
        fi
        # Also add to current session
        export PATH="$PLEXUS_BIN_DIR:$PATH"

        # Create /usr/local/bin symlink if we have sudo
        if [ "$EUID" -eq 0 ]; then
            ln -sf "$VENV_PLEXUS" /usr/local/bin/plexus
        elif sudo -n true 2>/dev/null; then
            sudo ln -sf "$VENV_PLEXUS" /usr/local/bin/plexus
        fi
    fi
fi

echo -e "  ${GREEN}✓ Installed${NC}"
echo ""

# Step 2: Pair the device
echo "─────────────────────────────────────────"
echo ""

if [ -n "$PAIRING_CODE" ]; then
    echo "  Pairing with code: $PAIRING_CODE"
    echo ""

    if [ -n "$DEVICE_NAME" ]; then
        plexus pair --code "$PAIRING_CODE"
    else
        plexus pair --code "$PAIRING_CODE"
    fi
else
    echo "  No pairing code provided."
    echo ""
    echo "  To pair this device:"
    echo ""
    echo "  1. Go to ${CYAN}https://app.plexus.company/fleet${NC}"
    echo "  2. Click \"Add Device\" to get a pairing code"
    echo "  3. Run: ${CYAN}plexus pair --code YOUR_CODE${NC}"
    echo ""
    echo "  Or run ${CYAN}plexus pair${NC} to sign in directly."
    echo ""

    # Check if already paired
    if plexus status 2>/dev/null | grep -q "Connected"; then
        echo -e "  ${GREEN}✓ Device is already paired${NC}"
        echo ""
    else
        echo "  Skipping pairing for now..."
        echo ""
    fi
fi

# Step 3: Set up systemd service (Linux only)
if [ "$OS" = "Linux" ] && [ "$SKIP_SERVICE" = false ]; then
    echo "─────────────────────────────────────────"
    echo ""
    echo "  Setting up auto-start service..."
    echo ""

    # Determine the user
    PLEXUS_USER=${SUDO_USER:-$USER}
    PLEXUS_HOME=$(eval echo ~$PLEXUS_USER)

    # Find plexus binary location (prefer venv path)
    if [ -f "$VENV_PLEXUS" ]; then
        PLEXUS_BIN="$VENV_PLEXUS"
    elif [ -f "/opt/plexus/venv/bin/plexus" ]; then
        PLEXUS_BIN="/opt/plexus/venv/bin/plexus"
    else
        PLEXUS_BIN=$(which plexus 2>/dev/null || echo "/usr/local/bin/plexus")
    fi

    # Create systemd service file
    SERVICE_FILE="/etc/systemd/system/plexus.service"

    # Check if we have sudo access
    if [ "$EUID" -eq 0 ] || sudo -n true 2>/dev/null; then
        sudo tee $SERVICE_FILE > /dev/null << EOF
[Unit]
Description=Plexus Agent
Documentation=https://docs.plexus.company
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$PLEXUS_USER
ExecStart=$PLEXUS_BIN run
Restart=always
RestartSec=10
Environment=PLEXUS_CONFIG_DIR=$PLEXUS_HOME/.plexus

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=plexus

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$PLEXUS_HOME/.plexus

[Install]
WantedBy=multi-user.target
EOF

        # Reload systemd and enable service
        sudo systemctl daemon-reload
        sudo systemctl enable plexus.service

        echo -e "  ${GREEN}✓ Service installed${NC}"
        echo ""
        echo "  Service commands:"
        echo "    sudo systemctl start plexus    # Start now"
        echo "    sudo systemctl stop plexus     # Stop"
        echo "    sudo systemctl status plexus   # Check status"
        echo "    journalctl -u plexus -f        # View logs"
        echo ""

        # Start the service if device is paired
        if plexus status 2>/dev/null | grep -q "Connected"; then
            echo "  Starting service..."
            sudo systemctl start plexus.service
            echo -e "  ${GREEN}✓ Service started${NC}"
            echo ""
        fi
    else
        echo -e "  ${YELLOW}Skipping service setup (no sudo access)${NC}"
        echo ""
        echo "  To set up auto-start manually, run with sudo:"
        echo "    curl -sL https://app.plexus.company/setup | sudo bash"
        echo ""
    fi
fi

# Done!
echo "─────────────────────────────────────────"
echo ""
echo -e "  ${GREEN}Setup complete!${NC}"
echo ""
echo "  Quick commands:"
echo "    plexus run       # Start agent (foreground)"
echo "    plexus status    # Check connection"
echo "    plexus scan      # List sensors"
echo ""
echo "  Dashboard: ${CYAN}https://app.plexus.company${NC}"
echo ""
