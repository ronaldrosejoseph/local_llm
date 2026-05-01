#!/bin/bash

# Configuration
APP_NAME="Local LLM"
BUNDLE_ID="com.local.llm.chat"
SOURCE_FILE="macos_app/app_wrapper.swift"
APP_PATH="${APP_NAME}.app"

echo "🚀 Building ${APP_NAME} for Apple Silicon..."

# 1. Clean previous build
rm -rf "${APP_PATH}"

# 2. Prepare Source (Inject current path)
CURRENT_DIR=$(pwd)
sed "s|PROJECT_PATH_PLACEHOLDER|${CURRENT_DIR}|g" "${SOURCE_FILE}" > "macos_app/app_wrapper_compiled.swift"

# 3. Create bundle structure
mkdir -p "${APP_PATH}/Contents/MacOS"
mkdir -p "${APP_PATH}/Contents/Resources"

# 4. Compile Swift code
swiftc "macos_app/app_wrapper_compiled.swift" \
    -o "${APP_PATH}/Contents/MacOS/${APP_NAME}" \
    -target arm64-apple-macosx14.0 \
    -O

# 5. Copy Icon
cp "assets/AppIcon.icns" "${APP_PATH}/Contents/Resources/AppIcon.icns"

# 6. Clean up temp source
rm "macos_app/app_wrapper_compiled.swift"

# 7. Create PkgInfo
echo "APPL????" > "${APP_PATH}/Contents/PkgInfo"

# 8. Create Info.plist
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
</dict>
</plist>
EOF

# 9. Make executable
chmod +x "${APP_PATH}/Contents/MacOS/${APP_NAME}"

echo "✅ Success! ${APP_PATH} created in the current directory."
echo "👉 You can now move it to your /Applications folder."
