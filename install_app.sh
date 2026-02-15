#!/bin/bash
# Install BTC Terminal as a macOS app in /Applications
# Usage: ./install_app.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="BTC Terminal"
APP_PATH="/Applications/${APP_NAME}.app"

echo "Installing ${APP_NAME} to ${APP_PATH}..."

# Create .app bundle
mkdir -p "${APP_PATH}/Contents/MacOS"
mkdir -p "${APP_PATH}/Contents/Resources"

# Info.plist
cat > "${APP_PATH}/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>launch</string>
    <key>CFBundleName</key>
    <string>BTC Terminal</string>
    <key>CFBundleIdentifier</key>
    <string>com.akula.btc-terminal</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
</dict>
</plist>
PLIST

# Launch script — opens Terminal and runs btc_terminal.py
cat > "${APP_PATH}/Contents/MacOS/launch" << LAUNCHER
#!/bin/bash
osascript <<'EOF'
tell application "Terminal"
    do script "cd ${SCRIPT_DIR} && python3 btc_terminal.py"
    activate
end tell
EOF
LAUNCHER
chmod +x "${APP_PATH}/Contents/MacOS/launch"

# Register with LaunchServices
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "${APP_PATH}" 2>/dev/null || true

echo ""
echo "✅ ${APP_NAME} installed!"
echo ""
echo "Launch it from:"
echo "  - Spotlight: search 'BTC Terminal'"
echo "  - Launchpad"
echo "  - Finder: /Applications/BTC Terminal.app"
echo ""
echo "To run on startup:"
echo "  System Settings > General > Login Items > + > BTC Terminal"
