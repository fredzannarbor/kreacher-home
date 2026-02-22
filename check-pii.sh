#!/bin/bash
# check-pii.sh — Scan for personally identifying information before committing
#
# Run this before pushing your fork to catch real IPs, phone numbers,
# usernames, tokens, and other PII that shouldn't be in a public repo.
#
# Usage: ./check-pii.sh [directory]

DIR="${1:-.}"
FOUND=0

echo "Scanning for PII in $DIR ..."
echo ""

# Skip binary files, .git, and this script itself
EXCLUDE="--exclude-dir=.git --exclude=check-pii.sh --exclude-dir=.venv --exclude-dir=__pycache__"

# ── Real (non-placeholder) IP addresses ──
# Matches IPs that aren't 192.168.x.x, 10.x.x.x, 127.x.x.x, or 0.0.0.0
# (those are commonly used as examples/placeholders)
echo "--- Private/Public IP addresses (non-example) ---"
REAL_IPS=$(grep -rn $EXCLUDE -E '\b[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b' "$DIR" \
    | grep -v '192\.168\.' \
    | grep -v '10\.\(0\|1\|2\|255\)\.' \
    | grep -v '127\.' \
    | grep -v '0\.0\.0\.0' \
    | grep -v '255\.255' \
    | grep -v 'http.*8001\|http.*8002' \
    | grep -v '# example\|# placeholder\|# dummy' \
    | grep -v '\.py:.*#.*<--')

if [ -n "$REAL_IPS" ]; then
    echo "$REAL_IPS"
    FOUND=1
else
    echo "  (none found)"
fi
echo ""

# ── Phone numbers ──
echo "--- Phone numbers ---"
PHONES=$(grep -rn $EXCLUDE -E '\+1[0-9]{10}|\b[0-9]{3}[-.)][0-9]{3}[-.)][0-9]{4}\b' "$DIR" \
    | grep -v '+15551234567' \
    | grep -v '+1555' \
    | grep -v '# example\|# placeholder\|# dummy\|<--')

if [ -n "$PHONES" ]; then
    echo "$PHONES"
    FOUND=1
else
    echo "  (none found)"
fi
echo ""

# ── Email addresses ──
echo "--- Email addresses ---"
EMAILS=$(grep -rn $EXCLUDE -oE '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}' "$DIR" \
    | grep -v 'example\.com\|noreply@\|placeholder')

if [ -n "$EMAILS" ]; then
    echo "$EMAILS"
    FOUND=1
else
    echo "  (none found)"
fi
echo ""

# ── Home directory paths with usernames ──
echo "--- Home directory paths (may contain username) ---"
HOMEPATHS=$(grep -rn $EXCLUDE -E '/Users/[a-zA-Z0-9]+|/home/[a-zA-Z0-9]+' "$DIR" \
    | grep -v '/Users/you\|/Users/your\|/home/you\|/home/your')

if [ -n "$HOMEPATHS" ]; then
    echo "$HOMEPATHS"
    FOUND=1
else
    echo "  (none found)"
fi
echo ""

# ── API tokens / secrets ──
echo "--- Potential API tokens or secrets ---"
TOKENS=$(grep -rn $EXCLUDE -iE '(token|secret|password|api_key|access_key)\s*[=:]\s*["\x27][^"\x27]{8,}' "$DIR" \
    | grep -v 'YOUR_TOKEN\|<YOUR\|example\|placeholder\|dummy\|TODO')

if [ -n "$TOKENS" ]; then
    echo "$TOKENS"
    FOUND=1
else
    echo "  (none found)"
fi
echo ""

# ── MAC addresses ──
echo "--- MAC addresses ---"
MACS=$(grep -rn $EXCLUDE -iE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' "$DIR")

if [ -n "$MACS" ]; then
    echo "$MACS"
    FOUND=1
else
    echo "  (none found)"
fi
echo ""

# ── Specific device model numbers (might identify your household) ──
echo "--- Specific device model numbers ---"
MODELS=$(grep -rn $EXCLUDE -iE 'QN[0-9]{2}Q[0-9]+|UN[0-9]{2}[A-Z]{2}[0-9]+|WM[0-9]{4}|DL[EGV][0-9]{4}|LDT[0-9]{4}' "$DIR")

if [ -n "$MODELS" ]; then
    echo "$MODELS"
    FOUND=1
else
    echo "  (none found)"
fi
echo ""

# ── Summary ──
echo "================================"
if [ "$FOUND" -eq 0 ]; then
    echo "No PII detected. Safe to push."
    exit 0
else
    echo "POTENTIAL PII FOUND — review the items above before pushing!"
    exit 1
fi
