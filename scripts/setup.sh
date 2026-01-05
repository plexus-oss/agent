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
echo ""

# Step 1: Install plexus-agent
echo "─────────────────────────────────────────"
echo ""
echo "  Installing Plexus agent..."
echo ""

# Use pip to install
if command -v pip3 &> /dev/null; then
    PIP=pip3
elif command -v pip &> /dev/null; then
    PIP=pip
else
    PIP="$PYTHON -m pip"
fi

# Install with sensor support by default on Linux (likely Raspberry Pi)
if [ "$OS" = "Linux" ]; then
    $PIP install --upgrade plexus-agent[sensors] --quiet 2>/dev/null || \
    $PIP install --upgrade plexus-agent --quiet
else
    $PIP install --upgrade plexus-agent --quiet
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

    # Find plexus binary location
    PLEXUS_BIN=$(which plexus 2>/dev/null || echo "/usr/local/bin/plexus")

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
