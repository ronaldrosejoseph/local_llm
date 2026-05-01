#!/bin/bash

# This script creates a macOS .icns file from a source image
SOURCE_IMAGE="assets/app_icon_source.png"
ICONSET_NAME="AppIcon.iconset"

if [ ! -f "$SOURCE_IMAGE" ]; then
    echo "Error: Source image $SOURCE_IMAGE not found."
    exit 1
fi

echo "🎨 Generating macOS icon set..."

mkdir -p "$ICONSET_NAME"

# Create all required sizes (correctly specified for iconutil)
sips -s format png -z 16 16     "$SOURCE_IMAGE" --out "$ICONSET_NAME/icon_16x16.png"
sips -s format png -z 32 32     "$SOURCE_IMAGE" --out "$ICONSET_NAME/icon_16x16@2x.png"
sips -s format png -z 32 32     "$SOURCE_IMAGE" --out "$ICONSET_NAME/icon_32x32.png"
sips -s format png -z 64 64     "$SOURCE_IMAGE" --out "$ICONSET_NAME/icon_32x32@2x.png"
sips -s format png -z 128 128   "$SOURCE_IMAGE" --out "$ICONSET_NAME/icon_128x128.png"
sips -s format png -z 256 256   "$SOURCE_IMAGE" --out "$ICONSET_NAME/icon_128x128@2x.png"
sips -s format png -z 256 256   "$SOURCE_IMAGE" --out "$ICONSET_NAME/icon_256x256.png"
sips -s format png -z 512 512   "$SOURCE_IMAGE" --out "$ICONSET_NAME/icon_256x256@2x.png"
sips -s format png -z 512 512   "$SOURCE_IMAGE" --out "$ICONSET_NAME/icon_512x512.png"
sips -s format png -z 1024 1024 "$SOURCE_IMAGE" --out "$ICONSET_NAME/icon_512x512@2x.png"

# Convert iconset to icns
iconutil -c icns "$ICONSET_NAME" -o assets/AppIcon.icns

# Cleanup
rm -rf "$ICONSET_NAME"

echo "✅ AppIcon.icns created."
