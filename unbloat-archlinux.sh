#!/bin/bash
set -euo pipefail

echo "============================================"
echo "   Arch Linux Storage Cleanup Script"
echo "============================================"

# ── Step 1: Clear pacman package cache ────────────────────────────────────────
echo ""
echo "[1/6] Cleaning pacman package cache..."
sudo pacman -Sc --noconfirm
echo "      Done."

# ── Step 2: Remove orphaned packages ──────────────────────────────────────────
echo ""
echo "[2/6] Checking for orphaned packages..."
orphans=$(pacman -Qdtq 2>/dev/null || true)

if [ -z "$orphans" ]; then
    echo "      No orphaned packages found."
else
    echo "      Orphaned packages found:"
    echo "$orphans" | sed 's/^/        - /'
    read -rp "      Remove these orphans? (y/N): " confirm_orphans
    if [[ $confirm_orphans =~ ^[Yy]$ ]]; then
        sudo pacman -Rns $orphans --noconfirm
        echo "      Orphaned packages removed."
    else
        echo "      Skipped."
    fi
fi

# ── Step 3: Clear systemd journal logs ────────────────────────────────────────
echo ""
echo "[3/6] Clearing journal logs (keeping last 7 days)..."
sudo journalctl --vacuum-time=7d
echo "      Done."

# ── Step 4: Clean AUR helper cache ────────────────────────────────────────────
echo ""
echo "[4/6] Cleaning AUR build cache..."
if command -v yay &>/dev/null; then
    echo "      Using yay..."
    yay -Sc --noconfirm
elif command -v paru &>/dev/null; then
    echo "      Using paru..."
    paru -Sc --noconfirm
else
    echo "      No AUR helper found (yay/paru). Skipping."
fi

# ── Step 5: Clear /tmp safely ─────────────────────────────────────────────────
echo ""
echo "[5/6] Cleaning /tmp..."
# Only remove entries older than 1 day to avoid killing active process sockets/locks
find /tmp -mindepth 1 -mtime +1 -exec rm -rf {} + 2>/dev/null || true
echo "      Done. (Only removed /tmp entries older than 1 day)"
#
# NOTE: /var/cache/ is intentionally NOT wiped here. It contains more than
# just pacman cache (font cache, ldconfig, app caches). Pacman's own cache
# is already handled in Step 1. Blindly doing `rm -rf /var/cache/*` risks
# breaking active processes and font/linker state.

# ── Step 6: List large files for manual review ────────────────────────────────
echo ""
echo "[6/6] Scanning for large files (>100MB) in common locations..."
echo "      Directories scanned: /home /var /opt /usr/local"
echo "      (Skipping /proc, /sys, /dev, /run to avoid virtual fs noise)"
echo ""

large_files=$(find /home /var /opt /usr/local \
    -type f -size +100M \
    -not \( -path "/proc/*" -o -path "/sys/*" -o -path "/dev/*" -o -path "/run/*" \) \
    -exec ls -lh {} \; 2>/dev/null \
    | awk '{ printf "%-12s %s\n", $5, $9 }' \
    | sort -rh)

if [ -z "$large_files" ]; then
    echo "      No large files found."
else
    echo "      SIZE         PATH"
    echo "      ─────────────────────────────────────────────────"
    echo "$large_files" | sed 's/^/      /'
    echo ""
    echo "      Review the list above and delete manually if not needed."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "============================================"
echo "   Cleanup complete."
echo "============================================"
