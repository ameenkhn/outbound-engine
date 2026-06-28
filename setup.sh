#!/usr/bin/env bash
# ============================================================================
# Outbound Engine — one-command local setup (macOS / Linux)
#
#   bash setup.sh
#
# Fixes the "No module named playwright / greenlet build failed" problem by
# building the virtualenv on a SUPPORTED Python (3.11–3.13), not 3.14. It:
#   1. finds a compatible Python (or installs 3.12 via Homebrew if available),
#   2. recreates ./.venv with it,
#   3. installs requirements + httpx,
#   4. downloads the Playwright Chromium browser (needed for the Meta scraper),
#   5. verifies the key imports work.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Outbound Engine setup starting…"

# --- 1. Find a compatible Python (3.11, 3.12, or 3.13) ----------------------
PY=""
CANDIDATES=(
  python3.12 python3.13 python3.11
  /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.11
  /usr/local/bin/python3.12 /usr/local/bin/python3.13 /usr/local/bin/python3.11
  /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12
  /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13
  /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11
)
for cand in "${CANDIDATES[@]}"; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done

# --- 2. None found? Install 3.12 via Homebrew, else guide the user ----------
if [ -z "$PY" ]; then
  if command -v brew >/dev/null 2>&1; then
    echo "==> No compatible Python found. Installing Python 3.12 via Homebrew…"
    brew install python@3.12
    PY="$(brew --prefix)/bin/python3.12"
  else
    echo ""
    echo "ERROR: Need Python 3.11, 3.12, or 3.13 (you appear to have only 3.14, which the"
    echo "scraper libraries don't support yet)."
    echo ""
    echo "Install Python 3.12 from:"
    echo "   https://www.python.org/downloads/release/python-3128/"
    echo "(download the macOS 64-bit installer, double-click, finish, then re-run: bash setup.sh)"
    exit 1
  fi
fi

echo "==> Using Python: $("$PY" --version 2>&1)  ($PY)"

# --- 3. Recreate the virtualenv --------------------------------------------
echo "==> Rebuilding ./.venv (removing any broken one)…"
rm -rf .venv
"$PY" -m venv .venv

echo "==> Upgrading pip…"
./.venv/bin/pip install --upgrade pip >/dev/null

echo "==> Installing Python dependencies…"
./.venv/bin/pip install -r requirements.txt

# --- 4. Download the Chromium browser Playwright drives ---------------------
echo "==> Downloading the Chromium browser (~150MB, one time)…"
./.venv/bin/python -m playwright install chromium

# --- 5. Verify -------------------------------------------------------------
echo "==> Verifying imports…"
./.venv/bin/python - <<'PYCHECK'
import importlib
for m in ("playwright", "httpx", "bs4"):
    importlib.import_module(m)
print("OK — playwright, httpx and beautifulsoup4 all import cleanly.")
PYCHECK

echo ""
echo "============================================================"
echo " ✅ Setup complete!"
echo ""
echo " Start the lead control panel with:"
echo "     .venv/bin/python -m sourcing.control_panel"
echo ""
echo " Then open http://127.0.0.1:8765 , check ONLY 'Meta Ad"
echo " Library', type a niche (e.g. 'fitness coach'), and Harvest."
echo " (Meta needs no API key. Give it 30s–2min — that's the"
echo "  real browser working.)"
echo "============================================================"
