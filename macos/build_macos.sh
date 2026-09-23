#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."
python3 build_ui.py
python3 -m unittest discover -s tests -p 'test*.py' -v
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements-macos.txt

# 从仓库内的方形 PNG 生成 macOS 应用图标。
ICONSET="build/ResearchBench.iconset"
ICON_SOURCE="build/ResearchBench-icon-source.png"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"
# 预览文件历史上由 ICO 生成器写出，虽然扩展名是 .png，内部格式可能仍是 ICO。
# 先显式转成真实 PNG，避免 sips 按 ICO 格式处理临时输出并报 Error 13。
sips -s format png "assets/预览_flask.png" --out "$ICON_SOURCE" >/dev/null
for spec in "16 icon_16x16" "32 icon_16x16@2x" "32 icon_32x32" \
            "64 icon_32x32@2x" "128 icon_128x128" "256 icon_128x128@2x" \
            "256 icon_256x256" "512 icon_256x256@2x" "512 icon_512x512" \
            "1024 icon_512x512@2x"; do
  size="${spec%% *}"
  name="${spec#* }"
  sips -s format png -z "$size" "$size" "$ICON_SOURCE" \
    --out "$ICONSET/$name.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "assets/ResearchBench.icns"

python3 -m PyInstaller --noconfirm --clean ResearchBench-macOS.spec

# 无开发者证书时做临时签名，确保应用包内部签名一致；公开分发仍建议正式签名和公证。
codesign --force --deep --sign - --entitlements "macos/entitlements.plist" \
  "dist/ResearchBench-v2.4.5.app"

rm -f "dist/ResearchBench-v2.4.5-macOS.dmg"
DMG_ROOT="build/dmg-root"
rm -rf "$DMG_ROOT"
mkdir -p "$DMG_ROOT"
cp -R "dist/ResearchBench-v2.4.5.app" "$DMG_ROOT/"
ln -s /Applications "$DMG_ROOT/Applications"
hdiutil create \
  -volname "ResearchBench 2.4.5" \
  -srcfolder "$DMG_ROOT" \
  -ov -format UDZO \
  "dist/ResearchBench-v2.4.5-macOS.dmg"

echo "Built dist/ResearchBench-v2.4.5.app"
echo "Built dist/ResearchBench-v2.4.5-macOS.dmg"
