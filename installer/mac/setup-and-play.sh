#!/bin/bash
# SoloStone on macOS: one-command setup and play.
# Run from the repository root:
#   bash installer/mac/setup-and-play.sh
set -eu

REPO="$(cd "$(dirname "$0")/../.." && pwd -P)"
cd "$REPO"

echo "=== SoloStone macOS 一键安装 ==="

# ---- prerequisites
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is required. Install it from https://python.org"
    exit 1
fi
if ! command -v dotnet &>/dev/null && ! ~/.dotnet/dotnet --version &>/dev/null; then
    echo "ERROR: .NET SDK is required. Install it from https://dotnet.microsoft.com"
    exit 1
fi
DOTNET="$(command -v dotnet || echo ~/.dotnet/dotnet)"

HS="/Applications/Hearthstone"
if [ ! -d "$HS" ]; then
    echo "Hearthstone not found at $HS."
    echo "Install it through Battle.net first, then run this script again."
    exit 1
fi

# ---- 1. extract game references
echo ""
echo "[1/4] Extracting game references from $HS..."
python3 tests/run.py --client "$HS" --suite run-tool || {
    echo "ERROR: could not extract game references. Is Hearthstone fully installed?"
    exit 1
}

# ---- 2. build plugin
echo ""
echo "[2/4] Building SoloStone plugin..."
$DOTNET build plugin/OfflineProbe.csproj -c Debug -p:EngineSource=true -p:Installer=true -p:NuGetAudit=false || {
    echo "ERROR: plugin build failed."
    exit 1
}

# ---- 3. quit Battle.net reminder
echo ""
echo "[3/4] Setup ready."
if pgrep -i "Battle.net" &>/dev/null; then
    echo "  ⚠ Battle.net is running. Quit it now (menu bar icon → Quit),"
    echo "    then press Enter to continue, or Ctrl+C to abort."
    read -r
fi

# ---- 4. setup
echo ""
echo "[4/4] Installing SoloStone (copying your Hearthstone, this takes about a minute)..."
python3 installer/mac/solostone.py setup --accept-warning || {
    echo "ERROR: setup failed."
    exit 1
}

# ---- done
echo ""
echo "=== ✅ SoloStone is installed! ==="
echo ""
echo "Launch it:"
echo "  python3 installer/mac/solostone.py launch"
echo ""
echo "Or build a double-click app (one time):"
echo "  python3 installer/mac/solostone.py app"
echo "  Then double-click SoloStone in Applications."
echo ""
