#!/bin/bash

# Build a self-contained "Local LLM.app" and package it into a .dmg
# The .dmg is what users download — they drag the app to /Applications and double-click.

set -e

APP_NAME="Local LLM"
BUNDLE_ID="com.local.llm.chat"
SWIFT_SOURCE="macos_app/app_wrapper.swift"
APP_PATH="${APP_NAME}.app"
DMG_NAME="${APP_NAME}.dmg"
PROJECT_BUNDLE_DIR="${APP_PATH}/Contents/Resources/project"

echo "🚀 Building ${APP_NAME}.app + .dmg..."

# 1. Clean previous build
rm -rf "${APP_PATH}"
rm -f "${DMG_NAME}"

# 2. Create bundle structure
mkdir -p "${APP_PATH}/Contents/MacOS"
mkdir -p "${APP_PATH}/Contents/Resources"
mkdir -p "${PROJECT_BUNDLE_DIR}"

# 3. Compile Swift app (no more path injection — uses Bundle.main.resourcePath at runtime)
echo "   Compiling Swift wrapper..."
swiftc "${SWIFT_SOURCE}" \
    -o "${APP_PATH}/Contents/MacOS/${APP_NAME}" \
    -target arm64-apple-macosx14.0 \
    -framework Speech \
    -O

# 4. Copy project files into the bundle (excluding dev/build artifacts)
echo "   Copying project into bundle..."
rsync -a \
    --exclude='.git' \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.app' \
    --exclude='*.dmg' \
    --exclude='database' \
    --exclude='server.log' \
    --exclude='server.pid' \
    --exclude='.startup_status' \
    --exclude='.requirements.hash' \
    --exclude='.init_db.hash' \
    --exclude='.server_lifecycle' \
    --exclude='.claude' \
    --exclude='CLAUDE.md' \
    --exclude='AGENTS.md' \
    --exclude='scratch' \
    --exclude='.DS_Store' \
    --exclude='make_app.sh' \
    --exclude='make_icon.sh' \
    --exclude='uninstall.sh' \
    --exclude='macos_app/app_wrapper_compiled.swift' \
    --exclude='worker.pid' \
    ./ "${PROJECT_BUNDLE_DIR}/"

# 5. Copy icon
if [ -f "assets/AppIcon.icns" ]; then
    cp "assets/AppIcon.icns" "${APP_PATH}/Contents/Resources/AppIcon.icns"
fi

# 6. Create PkgInfo
echo "APPL????" > "${APP_PATH}/Contents/PkgInfo"

# 7. Create Info.plist
cat <<EOF > "${APP_PATH}/Contents/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>${APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>${BUNDLE_ID}</string>
    <key>CFBundleName</key>
    <string>${APP_NAME}</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon.icns</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>14.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsLocalNetworking</key>
        <true/>
    </dict>
    <key>NSMicrophoneUsageDescription</key>
    <string>Local LLM needs microphone access for speech-to-text input.</string>
    <key>NSSpeechRecognitionUsageDescription</key>
    <string>Local LLM uses on-device speech recognition to transcribe your voice into chat messages.</string>
</dict>
</plist>
EOF

# 8. Make executable
chmod +x "${APP_PATH}/Contents/MacOS/${APP_NAME}"

# 9. Create .dmg
echo "   Creating .dmg..."
TMP_DMG="tmp_${APP_NAME}.dmg"

# Create a temporary directory for the .dmg layout
DMG_SRC=".dmg_src"
rm -rf "${DMG_SRC}"
mkdir -p "${DMG_SRC}"
cp -R "${APP_PATH}" "${DMG_SRC}/"
# Create a symlink to /Applications for drag-to-install
ln -s /Applications "${DMG_SRC}/Applications"

hdiutil create -volname "${APP_NAME}" \
    -srcfolder "${DMG_SRC}" \
    -ov -format UDZO \
    "${DMG_NAME}" \
    > /dev/null

# Cleanup
rm -rf "${TMP_DMG}" "${DMG_SRC}"

echo ""
echo "✅ Done!"
echo ""
echo "   📦  ${DMG_NAME}  — distribute this to users"
echo "   📁  ${APP_PATH}  — local app bundle (for testing)"
echo ""
echo "User experience:"
echo "   1. Download ${DMG_NAME}"
echo "   2. Open it, drag '${APP_NAME}' to /Applications"
echo "   3. Double-click — first launch installs Python deps + downloads a model"
echo "      (progress shown in the loading screen)"
echo ""
