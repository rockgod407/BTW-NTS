#!/bin/bash
# ──────────────────────────────────────────────────────────────
# nettest installer — run this on any Mac to install everything
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/rockgod407/BTW-NTS/main/install.sh | bash
#   — OR —
#   bash install.sh
# ──────────────────────────────────────────────────────────────

set -e

REPO="https://github.com/rockgod407/BTW-NTS.git"
BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

echo ""
echo -e "${BOLD}━━━ nettest installer ━━━${RESET}"
echo ""

# ── Step 1: Find Python 3 ──────────────────────────────────────
PYTHON=""
for candidate in python3 /usr/bin/python3 /Library/Developer/CommandLineTools/usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "${RED}Error: Python 3 not found.${RESET}"
    echo "Install Xcode Command Line Tools first:"
    echo "  xcode-select --install"
    exit 1
fi

PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo -e "  Python:  ${GREEN}$PYTHON${RESET} (${PY_VERSION})"

# ── Step 2: Upgrade pip ────────────────────────────────────────
echo ""
echo -e "${BOLD}Upgrading pip...${RESET}"
"$PYTHON" -m pip install --upgrade pip --user --quiet 2>/dev/null || \
"$PYTHON" -m pip install --upgrade pip --quiet 2>/dev/null || \
echo -e "${YELLOW}  (pip upgrade skipped — continuing with current version)${RESET}"

PIP_VERSION=$("$PYTHON" -m pip --version 2>/dev/null | head -1)
echo -e "  pip:     ${GREEN}${PIP_VERSION}${RESET}"

# ── Step 3: Install nettest ────────────────────────────────────
echo ""
echo -e "${BOLD}Installing nettest...${RESET}"
"$PYTHON" -m pip install --user "git+${REPO}" 2>&1 | tail -5

# ── Step 4: Ensure PATH includes Python user bin ───────────────
USER_BIN=$("$PYTHON" -c "import site; print(site.getusersitepackages().replace('/lib/python/site-packages', '/bin').replace('lib/python${PY_VERSION}/site-packages', 'bin'))")
# More reliable: ask Python directly
USER_BIN=$("$PYTHON" -c "
import sysconfig
print(sysconfig.get_path('scripts', scheme='posix_user') if hasattr(sysconfig, 'get_path') else '')
" 2>/dev/null)

# Fallback detection
if [ -z "$USER_BIN" ] || [ ! -d "$USER_BIN" ]; then
    USER_BIN="$HOME/Library/Python/${PY_VERSION}/bin"
fi

echo ""

# Check if nettest is already findable
if command -v nettest &>/dev/null; then
    echo -e "${GREEN}${BOLD}nettest is ready!${RESET}"
    echo ""
    nettest doctor 2>/dev/null || true
elif [ -f "${USER_BIN}/nettest" ]; then
    echo -e "${YELLOW}nettest installed but ${USER_BIN} is not on your PATH.${RESET}"
    echo ""

    # Detect shell config file
    SHELL_NAME=$(basename "$SHELL")
    if [ "$SHELL_NAME" = "zsh" ]; then
        RC_FILE="$HOME/.zshrc"
    elif [ "$SHELL_NAME" = "bash" ]; then
        RC_FILE="$HOME/.bash_profile"
    else
        RC_FILE="$HOME/.profile"
    fi

    # Add to PATH if not already there
    if ! grep -q "$USER_BIN" "$RC_FILE" 2>/dev/null; then
        echo "" >> "$RC_FILE"
        echo "# Added by nettest installer" >> "$RC_FILE"
        echo "export PATH=\"${USER_BIN}:\$PATH\"" >> "$RC_FILE"
        echo -e "${GREEN}Added ${USER_BIN} to PATH in ${RC_FILE}${RESET}"
        echo ""
        echo -e "${BOLD}Restart your terminal or run:${RESET}"
        echo -e "  source ${RC_FILE}"
    else
        echo -e "${YELLOW}PATH entry already exists in ${RC_FILE}${RESET}"
        echo ""
        echo -e "${BOLD}Restart your terminal or run:${RESET}"
        echo -e "  source ${RC_FILE}"
    fi
    echo ""
    echo "Then test with:"
    echo "  nettest doctor"
else
    echo -e "${RED}Something went wrong — nettest binary not found at ${USER_BIN}/nettest${RESET}"
    echo ""
    echo "Try running manually:"
    echo "  $PYTHON -m pip install --user git+${REPO}"
    echo ""
    echo "Or run directly with:"
    echo "  $PYTHON -m nettest.cli doctor"
    exit 1
fi

echo ""
echo -e "${BOLD}━━━ Done! ━━━${RESET}"
echo ""
