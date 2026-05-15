#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO_ROOT="${SCRIPT_DIR:h}"
APP_NAME="Ayla"
APP_EXECUTABLE="Ayla"
BUNDLE_ID="${AYLA_APP_BUNDLE_ID:-local.ayla.workspace}"
OUTPUT_PATH="${AYLA_APP_OUTPUT:-$REPO_ROOT/dist/$APP_NAME.app}"
INSTALL_ROOT="${AYLA_INSTALL_ROOT:-$HOME/Library/Application Support/Ayla}"
APP_ICON_SOURCE="${AYLA_APP_ICON_SOURCE:-$REPO_ROOT/macos/AylaClient/AppIcon.png}"
APP_ICON_BASENAME="AylaAppIcon"
OPEN_AFTER=0

usage() {
  cat <<'EOF'
Usage: scripts/build_macos_client.sh [options]

Build the native Ayla macOS client. The client is a Swift AppKit shell that
starts Ayla Core and renders the workspace in WKWebView.

Options:
  --output PATH         Write the .app bundle to PATH.
  --install-root PATH   Embed the Ayla install root used by the app.
  --icon PATH           Use PATH as the source 1024px PNG icon.
  --open                Open the app after building.
  -h, --help            Show this help.
EOF
}

make_app_icon() {
  local source="$1"
  local iconset="$2"
  local output="$3"
  local icon_specs=(
    "16 icon_16x16.png"
    "32 icon_16x16@2x.png"
    "32 icon_32x32.png"
    "64 icon_32x32@2x.png"
    "128 icon_128x128.png"
    "256 icon_128x128@2x.png"
    "256 icon_256x256.png"
    "512 icon_256x256@2x.png"
    "512 icon_512x512.png"
    "1024 icon_512x512@2x.png"
  )
  local spec size filename

  rm -rf "$iconset"
  mkdir -p "$iconset"

  for spec in "${icon_specs[@]}"; do
    size="${spec%% *}"
    filename="${spec#* }"
    sips -z "$size" "$size" "$source" --out "$iconset/$filename" >/dev/null
  done

  if ! iconutil -c icns "$iconset" -o "$output" 2>/dev/null; then
    python3 "$REPO_ROOT/scripts/generate_app_icon.py" --icns-from-iconset "$iconset" "$output" >/dev/null
  fi
  rm -rf "$iconset"
}

while (( $# )); do
  case "$1" in
    --output)
      if (( $# < 2 )); then
        echo "Missing value for --output" >&2
        exit 2
      fi
      OUTPUT_PATH="$2"
      shift 2
      ;;
    --output=*)
      OUTPUT_PATH="${1#--output=}"
      shift
      ;;
    --install-root)
      if (( $# < 2 )); then
        echo "Missing value for --install-root" >&2
        exit 2
      fi
      INSTALL_ROOT="$2"
      shift 2
      ;;
    --install-root=*)
      INSTALL_ROOT="${1#--install-root=}"
      shift
      ;;
    --icon)
      if (( $# < 2 )); then
        echo "Missing value for --icon" >&2
        exit 2
      fi
      APP_ICON_SOURCE="$2"
      shift 2
      ;;
    --icon=*)
      APP_ICON_SOURCE="${1#--icon=}"
      shift
      ;;
    --open)
      OPEN_AFTER=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$OSTYPE" != darwin* ]]; then
  echo "The Ayla macOS client can only be built on macOS." >&2
  exit 1
fi

if ! command -v swiftc >/dev/null 2>&1; then
  echo "Missing swiftc. Install Xcode Command Line Tools first: xcode-select --install" >&2
  exit 1
fi

SOURCE_FILE="$REPO_ROOT/macos/AylaClient/main.swift"
if [[ ! -f "$SOURCE_FILE" ]]; then
  echo "Missing client source: $SOURCE_FILE" >&2
  exit 1
fi

OUTPUT_PATH="${OUTPUT_PATH:A}"
INSTALL_ROOT="${INSTALL_ROOT:A}"
APP_ICON_SOURCE="${APP_ICON_SOURCE:A}"
CONTENTS_DIR="$OUTPUT_PATH/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
MODULE_CACHE_DIR="${TMPDIR:-/tmp}/ayla-swift-module-cache"

if [[ ! -f "$APP_ICON_SOURCE" ]]; then
  ICON_GENERATOR="$REPO_ROOT/scripts/generate_app_icon.py"
  if [[ ! -f "$ICON_GENERATOR" ]]; then
    echo "Missing app icon source and generator: $APP_ICON_SOURCE" >&2
    exit 1
  fi
  python3 "$ICON_GENERATOR" "$APP_ICON_SOURCE" >/dev/null
fi

rm -rf "$OUTPUT_PATH"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR" "$MODULE_CACHE_DIR"
export CLANG_MODULE_CACHE_PATH="$MODULE_CACHE_DIR"

swiftc -O \
  -module-cache-path "$MODULE_CACHE_DIR" \
  -framework AppKit \
  -framework WebKit \
  "$SOURCE_FILE" \
  -o "$MACOS_DIR/$APP_EXECUTABLE"

chmod +x "$MACOS_DIR/$APP_EXECUTABLE"
printf '%s\n' "$INSTALL_ROOT" > "$RESOURCES_DIR/AylaInstallRoot.txt"
printf 'APPL????' > "$CONTENTS_DIR/PkgInfo"

if [[ -f "$APP_ICON_SOURCE" ]]; then
  if command -v sips >/dev/null 2>&1; then
    make_app_icon \
      "$APP_ICON_SOURCE" \
      "$RESOURCES_DIR/$APP_ICON_BASENAME.iconset" \
      "$RESOURCES_DIR/$APP_ICON_BASENAME.icns"
  else
    echo "Skipping app icon generation because sips is unavailable." >&2
  fi
fi

cat > "$CONTENTS_DIR/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleDisplayName</key>
  <string>$APP_NAME</string>
  <key>CFBundleExecutable</key>
  <string>$APP_EXECUTABLE</string>
  <key>CFBundleIconFile</key>
  <string>$APP_ICON_BASENAME</string>
  <key>CFBundleIdentifier</key>
  <string>$BUNDLE_ID</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>$APP_NAME</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSApplicationCategoryType</key>
  <string>public.app-category.productivity</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
EOF

if command -v plutil >/dev/null 2>&1; then
  plutil -lint "$CONTENTS_DIR/Info.plist" >/dev/null
fi

if [[ "${AYLA_SKIP_CODESIGN:-0}" != "1" ]] && command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$OUTPUT_PATH" >/dev/null 2>&1 || true
fi

echo "Built $OUTPUT_PATH"
echo "Install root $INSTALL_ROOT"

if (( OPEN_AFTER )); then
  open "$OUTPUT_PATH"
fi
