#!/bin/bash
#
# Plexus Agent Setup Script
#
# Usage:
#   curl -sL https://app.plexus.company/setup | bash -s -- --key plx_abc123
#
# This script:
#   1. Installs Python if missing
#   2. Installs the Plexus agent
#   3. Configures API key or signs in via browser
#
# Note: The canonical version of this script is served from the frontend
# at app/setup/route.ts. This copy is for reference/offline use.
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Parse arguments
API_KEY=""
DEVICE_NAME=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --api-key|--key|-k)
            API_KEY="$2"
            shift 2
            ;;
        --name|-n)
            DEVICE_NAME="$2"
            shift 2
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

# Check for Python — auto-install if missing
if command -v python3 &> /dev/null; then
    PYTHON=python3
elif command -v python &> /dev/null; then
    PYTHON=python
else
    echo -e "  ${YELLOW}Python not found — installing...${NC}"
    if command -v apt-get &> /dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y -q python3 python3-pip
    elif command -v yum &> /dev/null; then
        sudo yum install -y -q python3 python3-pip
    elif command -v brew &> /dev/null; then
        brew install python3
    else
        echo -e "  ${RED}Could not install Python automatically${NC}"
        echo -e "  ${CYAN}Install Python 3.8+ manually, then re-run this script${NC}"
        echo ""
        exit 1
    fi

    # Re-detect after install
    if command -v python3 &> /dev/null; then
        PYTHON=python3
    elif command -v python &> /dev/null; then
        PYTHON=python
    else
        echo -e "  ${RED}Python installation failed${NC}"
        echo ""
        exit 1
    fi
    echo -e "  ${GREEN}✓ Python installed${NC}"
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

# Step 1: Install plexus-python
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

# Install with hardware support by default on Linux (likely Raspberry Pi)
if [ "$OS" = "Linux" ]; then
    "$VENV_PIP" install --upgrade pip --quiet

    # Detect Pi camera support
    IS_PI=false
    if [ -f /proc/device-tree/model ] && grep -qi "raspberry" /proc/device-tree/model 2>/dev/null; then
        IS_PI=true
    fi

    if [ "$IS_PI" = true ]; then
        # Pi gets sensors + camera by default
        "$VENV_PIP" install --upgrade "plexus-python[sensors,picamera]" --quiet 2>/dev/null || \
        "$VENV_PIP" install --upgrade "plexus-python[sensors]" --quiet 2>/dev/null || \
        "$VENV_PIP" install --upgrade plexus-python --quiet
    else
        "$VENV_PIP" install --upgrade "plexus-python[sensors]" --quiet 2>/dev/null || \
        "$VENV_PIP" install --upgrade plexus-python --quiet
    fi
else
    # macOS/other - use system pip or venv
    if [ -f "$VENV_PIP" ]; then
        "$VENV_PIP" install --upgrade pip --quiet
        "$VENV_PIP" install --upgrade plexus-python --quiet
    else
        # Fall back to system pip on macOS
        pip3 install --upgrade plexus-python --quiet 2>/dev/null || \
        $PYTHON -m pip install --upgrade plexus-python --quiet
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

# Step 2: Enable I2C and install diagnostic tools (Linux only)
echo "─────────────────────────────────────────"
echo ""

if [ "$OS" = "Linux" ]; then
    echo "  Setting up hardware support..."
    echo ""

    # Install system packages for I2C and camera support
    PKGS_TO_INSTALL=""
    command -v i2cdetect &> /dev/null || PKGS_TO_INSTALL="i2c-tools"

    # libcap-dev is needed by picamera2's python-prctl dependency
    if ! dpkg -s libcap-dev &> /dev/null 2>&1; then
        PKGS_TO_INSTALL="$PKGS_TO_INSTALL libcap-dev"
    fi

    if [ -n "$PKGS_TO_INSTALL" ]; then
        echo "  Installing system packages: $PKGS_TO_INSTALL"
        if [ "$EUID" -eq 0 ]; then
            apt-get install -y -qq $PKGS_TO_INSTALL
        elif sudo -n true 2>/dev/null; then
            sudo apt-get install -y -qq $PKGS_TO_INSTALL
        else
            echo -e "  ${YELLOW}Could not install system packages automatically.${NC}"
            echo "  Run manually: sudo apt install $PKGS_TO_INSTALL"
        fi
    fi

    # Add user to i2c group for sensor access without sudo
    if getent group i2c &> /dev/null && ! id -nG | grep -qw i2c; then
        if [ "$EUID" -eq 0 ]; then
            usermod -aG i2c "$USER"
            echo -e "  ${GREEN}✓ Added $USER to i2c group${NC}"
        elif sudo -n true 2>/dev/null; then
            sudo usermod -aG i2c "$USER"
            echo -e "  ${GREEN}✓ Added $USER to i2c group${NC}"
        else
            echo -e "  ${YELLOW}Add yourself to the i2c group: sudo usermod -aG i2c \$USER${NC}"
        fi
    fi

    # Enable I2C interface if raspi-config is available
    if command -v raspi-config &> /dev/null; then
        if ! grep -q "^dtparam=i2c_arm=on" /boot/config.txt 2>/dev/null && \
           ! grep -q "^dtparam=i2c_arm=on" /boot/firmware/config.txt 2>/dev/null; then
            echo -e "  ${YELLOW}I2C may not be enabled.${NC}"
            echo "  Enable it with: sudo raspi-config → Interface Options → I2C"
            echo ""
        fi
    fi

    # Scan I2C bus and show detected devices
    if command -v i2cdetect &> /dev/null; then
        echo "  Scanning I2C bus for connected sensors..."
        echo ""
        i2cdetect -y 1 2>/dev/null && echo "" || echo -e "  ${YELLOW}I2C bus scan failed — is I2C enabled?${NC}"

        echo "  Common sensor addresses:"
        echo "    0x68 = MPU6050/MPU9250 (IMU)"
        echo "    0x76 = BME280 (temp/humidity/pressure)"
        echo "    0x48 = PCF8591 (ADC)"
        echo ""
    fi

    echo -e "  ${GREEN}✓ I2C ready${NC}"
    echo ""
fi

# Step 3: Authenticate the device
echo "─────────────────────────────────────────"
echo ""

if [ -n "$API_KEY" ]; then
    # API key flow — write config and resolve org_id from the key
    mkdir -p "$HOME/.plexus"

    # Resolve org_id from API key so the agent knows which org to connect to
    ENDPOINT="https://app.plexus.company"
    ORG_ID=""
    if command -v curl &> /dev/null; then
        ORG_ID=$(curl -s -H "x-api-key: $API_KEY" "$ENDPOINT/api/auth/verify-key" 2>/dev/null | $PYTHON -c "import sys,json; print(json.load(sys.stdin).get('org_id',''))" 2>/dev/null)
    fi

    # Use device name as source_id if provided, otherwise use hostname
    SOURCE_ID="${DEVICE_NAME:-$(hostname)}"

    if [ -n "$ORG_ID" ]; then
        echo "{\"api_key\":\"$API_KEY\",\"endpoint\":\"$ENDPOINT\",\"org_id\":\"$ORG_ID\",\"source_id\":\"$SOURCE_ID\"}" > "$HOME/.plexus/config.json"
    else
        echo "{\"api_key\":\"$API_KEY\",\"endpoint\":\"$ENDPOINT\",\"source_id\":\"$SOURCE_ID\"}" > "$HOME/.plexus/config.json"
    fi

    export PLEXUS_API_KEY="$API_KEY"
    echo -e "  ${GREEN}✓ API key configured${NC}"
    if [ -n "$ORG_ID" ]; then
        echo -e "  ${GREEN}✓ Organization resolved${NC}"
    fi
    echo ""
else
    echo "  No API key provided."
    echo ""
    echo "  To authenticate this device:"
    echo ""
    echo "  1. Get an API key from ${CYAN}https://app.plexus.company${NC} → Settings → Developer"
    echo "  2. Run: ${CYAN}plexus start --key plx_xxxxx${NC}"
    echo ""
    echo "  Run ${CYAN}plexus start${NC} to sign in and connect."
    echo ""
fi

# Done!
echo "─────────────────────────────────────────"
echo ""
echo -e "  ${GREEN}Setup complete!${NC}"
echo ""
echo "  Quick commands:"
echo "    plexus start     # Set up and stream"
echo "    plexus reset     # Clear config and start over"
echo ""
echo "  Dashboard: ${CYAN}https://app.plexus.company${NC}"
echo ""
echo "  To uninstall:"
echo "    rm -rf ~/.plexus /opt/plexus"
echo ""
