#!/bin/bash
# start.sh — Local launcher for the Autounattend Generator (dev mode)
# Runs Flask directly without a container. Requires: python3, flask, genisoimage.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Check dependencies
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found"
    echo "Install with: sudo zypper install python3"
    exit 1
fi

if ! command -v genisoimage &>/dev/null && ! command -v mkisofs &>/dev/null; then
    echo "Warning: genisoimage/mkisofs not found — Build ISO will not work"
    echo "  Install with: sudo zypper install genisoimage"
fi

if ! python3 -c "import flask" 2>/dev/null; then
    echo "Error: flask not installed"
    echo "Install with: pip install flask"
    exit 1
fi

# Create local dirs
mkdir -p "$SCRIPT_DIR/configs"
mkdir -p "$SCRIPT_DIR/iso"
mkdir -p "$SCRIPT_DIR/xml"
mkdir -p "$SCRIPT_DIR/drivers"
mkdir -p "$SCRIPT_DIR/images"

export CONFIGS_DIR="$SCRIPT_DIR/configs"
export OUTPUT_DIR="$SCRIPT_DIR/iso"
export XML_DIR="$SCRIPT_DIR/xml"
export DRIVERS_DIR="$SCRIPT_DIR/drivers"
export IMAGES_DIR="$SCRIPT_DIR/images"

echo "Starting Autounattend Generator on http://localhost:8098"
echo "  Configs:  $CONFIGS_DIR"
echo "  ISO:      $OUTPUT_DIR"
echo "  XML:      $XML_DIR"
echo "  Drivers:  $DRIVERS_DIR"
echo "  Images:   $IMAGES_DIR"
echo ""

cd "$SCRIPT_DIR"
exec python3 app.py
