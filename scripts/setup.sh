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

# --name is required so every device lands on a deliberate, unique identifier.
# Previously we fell back to $(hostname), which silently merged streams on
# cloned SD-card images where every device shared a hostname. If --name is
# missing, prompt interactively in a TTY or fail loudly in non-interactive
# contexts. Validation keeps the name safe for Redis keys, URLs, and logs.
validate_device_name() {
    local name="$1"
    if [[ ! "$name" =~ ^[a-z0-9][a-z0-9_-]{1,62}$ ]]; then
        echo -e "  ${RED}Invalid device name: \"$name\"${NC}"
        echo "  Name must start with a letter or digit and contain only"
        echo "  lowercase letters, digits, '-', or '_' (2-63 chars total)."
        return 1
    fi
    return 0
}

if [ -z "$DEVICE_NAME" ]; then
    if [ -t 0 ]; then
        echo ""
        echo "  Every device needs a unique name (e.g. drone-01, greenhouse-north)."
        while [ -z "$DEVICE_NAME" ]; do
            read -rp "  Device name: " DEVICE_NAME
            if [ -n "$DEVICE_NAME" ] && ! validate_device_name "$DEVICE_NAME"; then
                DEVICE_NAME=""
            fi
        done
    else
        echo -e "  ${RED}Error: --name is required${NC}" >&2
        echo "  Example: curl -sL app.plexus.company/setup | bash -s -- --key plx_... --name drone-01" >&2
        exit 1
    fi
elif ! validate_device_name "$DEVICE_NAME"; then
    exit 1
fi

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
    mkdir -p "$HOME/.plexus"
    ENDPOINT="https://app.plexus.company"
    # install_id is intentionally NOT written here. The SDK generates it
    # lazily on first run (plexus.config.get_install_id) so that pre-baked
    # SD-card images get distinct install_ids per boot rather than sharing
    # whatever we'd stamp here.
    echo "{\"api_key\":\"$API_KEY\",\"endpoint\":\"$ENDPOINT\",\"source_id\":\"$DEVICE_NAME\"}" > "$HOME/.plexus/config.json"

    export PLEXUS_API_KEY="$API_KEY"
    echo -e "  ${GREEN}✓ API key configured${NC}"
    echo -e "  ${GREEN}✓ Device name: ${CYAN}$DEVICE_NAME${NC}"
    echo "    (the gateway may auto-suffix this if the name is already taken;"
    echo "     the assigned name will be logged on first connect)"
    echo ""
else
    echo "  No API key provided."
    echo ""
    echo "  To authenticate this device:"
    echo ""
    echo "  1. Get an API key from ${CYAN}https://app.plexus.company${NC} → Settings → Developer"
    echo "  2. Re-run this installer with: ${CYAN}--key plx_xxxxx --name $DEVICE_NAME${NC}"
    echo ""
fi

# Done!
echo "─────────────────────────────────────────"
echo ""
echo -e "  ${GREEN}Setup complete!${NC}"
echo ""
echo "  Dashboard: ${CYAN}https://app.plexus.company${NC}"
echo ""
echo "  To uninstall:"
echo "    rm -rf ~/.plexus /opt/plexus"
echo ""
