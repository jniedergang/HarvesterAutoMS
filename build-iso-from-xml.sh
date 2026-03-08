#!/bin/bash
# build-iso-from-xml.sh — Build autounattend ISO from a pre-generated XML
# Used inside the container where VMDP drivers are pre-extracted.
#
# Usage: ./build-iso-from-xml.sh [xml-file] [output-iso] [driver-paths]
#   driver-paths: colon-separated list of directories (e.g. /app/drivers/vmdp:/app/drivers/custom)
#   If omitted, falls back to legacy $SCRIPT_DIR/vmdp-drivers

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
XML_FILE="${1:-$SCRIPT_DIR/autounattend.xml}"
OUTPUT_ISO="${2:-/output/autounattend.iso}"
DRIVER_PATHS="${3:-}"
STAGING_DIR="$(mktemp -d)"

trap "rm -rf $STAGING_DIR" EXIT

if [ ! -f "$XML_FILE" ]; then
    echo "Error: XML file not found: $XML_FILE"
    exit 1
fi

echo "=== Building autounattend ISO ==="
echo "  XML:    $XML_FILE"
echo "  Output: $OUTPUT_ISO"

# Copy XML
cp "$XML_FILE" "$STAGING_DIR/autounattend.xml"
echo "XML copied to staging"

# Copy drivers into $WinPEDriver$/
DRIVER_DIR="$STAGING_DIR/\$WinPEDriver\$"
mkdir -p "$DRIVER_DIR"

TOTAL_INF=0

if [ -n "$DRIVER_PATHS" ]; then
    # Multi-source mode: colon-separated paths
    IFS=':' read -ra SOURCES <<< "$DRIVER_PATHS"
    for SRC in "${SOURCES[@]}"; do
        if [ -d "$SRC" ] && [ "$(ls -A "$SRC" 2>/dev/null)" ]; then
            SRC_NAME="$(basename "$SRC")"
            cp -r "$SRC/"* "$DRIVER_DIR/"
            INF_COUNT=$(find "$SRC" -maxdepth 1 -name '*.inf' 2>/dev/null | wc -l)
            TOTAL_INF=$((TOTAL_INF + INF_COUNT))
            echo "Drivers [$SRC_NAME] copied ($INF_COUNT .inf files)"
        else
            echo "Warning: Driver source empty or missing: $SRC"
        fi
    done
else
    # Legacy fallback: single vmdp-drivers directory
    DRIVER_SRC="$SCRIPT_DIR/vmdp-drivers"
    if [ -d "$DRIVER_SRC" ] && [ "$(ls -A "$DRIVER_SRC" 2>/dev/null)" ]; then
        cp -r "$DRIVER_SRC/"* "$DRIVER_DIR/"
        INF_COUNT=$(find "$DRIVER_SRC" -maxdepth 1 -name '*.inf' 2>/dev/null | wc -l)
        TOTAL_INF=$INF_COUNT
        echo "Drivers VMDP copied ($INF_COUNT .inf files)"
    else
        echo "Warning: No VMDP drivers found at $DRIVER_SRC"
        echo "  ISO will be created without drivers"
    fi
fi

echo "Total driver .inf files: $TOTAL_INF"

# Create output directory if needed
mkdir -p "$(dirname "$OUTPUT_ISO")"

# Find ISO tool
if command -v mkisofs &>/dev/null; then
    MKISO="mkisofs"
elif command -v genisoimage &>/dev/null; then
    MKISO="genisoimage"
else
    echo "Error: mkisofs or genisoimage not found"
    exit 1
fi

# Build ISO
echo ""
echo "=== Creating ISO ==="
$MKISO -J -r -V "OEMDRV" -o "$OUTPUT_ISO" "$STAGING_DIR"

echo ""
echo "ISO created: $OUTPUT_ISO ($(du -h "$OUTPUT_ISO" | cut -f1))"
echo ""
echo "Contents:"
echo "  autounattend.xml      — unattended install config"
echo "  \$WinPEDriver\$/       — VirtIO drivers (auto-loaded by Windows PE)"
