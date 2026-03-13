#!/bin/bash
# =============================================================================
# CH POS — Remote Bench Installation Script
# =============================================================================
# Run this from INSIDE the bench root directory (e.g. ~/erpnext-bench):
#
#   cd ~/erpnext-bench
#   bash apps/ch_pos/install_on_bench.sh
#
# What it does:
#   1. Patches a Frappe esbuild bug that causes "paths[0] undefined" when
#      bench get-app runs bench build before adding the app to apps.txt.
#   2. Gets all 3 custom apps with --skip-assets to avoid the bug entirely.
#   3. Builds all assets in one shot (after apps.txt is updated).
#   4. Prompts for the site name, then installs + migrates.
# =============================================================================

set -e

BENCH_DIR="$(pwd)"
FRAPPE_ESBUILD="$BENCH_DIR/apps/frappe/esbuild/utils.js"

echo ""
echo "============================================="
echo " CH POS — Bench Installation"
echo "============================================="
echo ""

# ── Step 1: Patch Frappe esbuild (idempotent) ─────────────────────────────
echo "[1/5] Patching Frappe esbuild to handle app install order..."

if grep -q "public_paths\[app\] ||" "$FRAPPE_ESBUILD"; then
    echo "      ✔ Patch already applied, skipping."
else
    sed -i 's/const get_public_path = (app) => public_paths\[app\];/const get_public_path = (app) => public_paths[app] || require("path").resolve(apps_path, app, app, "public");/' \
        "$FRAPPE_ESBUILD"
    echo "      ✔ Patch applied to $FRAPPE_ESBUILD"
fi

# ── Step 2: Get apps (skip-assets avoids the timing bug) ──────────────────
echo ""
echo "[2/5] Installing custom apps (--skip-assets)..."

bench get-app https://github.com/itgostack-ux/ch_item_master.git --skip-assets
bench get-app https://github.com/itgostack-ux/ch_erp_buyback.git --skip-assets
# ch_pos is already cloned (you're running this script from it), so skip if present
if [ ! -d "$BENCH_DIR/apps/ch_pos" ]; then
    bench get-app https://github.com/itgostack-ux/ch_pos.git --skip-assets
else
    echo "      ch_pos already present, skipping clone."
fi

echo "      ✔ All apps cloned and pip-installed."

# ── Step 3: Build all assets in one shot ──────────────────────────────────
echo ""
echo "[3/5] Building all assets (all apps now in apps.txt)..."
bench build
echo "      ✔ Build complete."

# ── Step 4: Site installation ─────────────────────────────────────────────
echo ""
echo "[4/5] Site installation"
echo "      Available sites:"
ls "$BENCH_DIR/sites" | grep -v assets | grep -v apps.txt | grep -v common_site_config.json || true
echo ""
read -rp "      Enter site name (e.g. qa.localhost): " SITE_NAME

if [ -z "$SITE_NAME" ]; then
    echo "      No site name entered. Skipping site install."
    echo "      Run manually:"
    echo "        bench --site <site> install-app ch_item_master"
    echo "        bench --site <site> install-app ch_erp_buyback"
    echo "        bench --site <site> install-app ch_pos"
    echo "        bench --site <site> migrate"
else
    echo ""
    echo "      Installing apps on site: $SITE_NAME"
    bench --site "$SITE_NAME" install-app ch_item_master
    bench --site "$SITE_NAME" install-app ch_erp_buyback
    bench --site "$SITE_NAME" install-app ch_pos
    bench --site "$SITE_NAME" migrate
    echo "      ✔ Apps installed and migrated."
fi

# ── Step 5: Restart ───────────────────────────────────────────────────────
echo ""
echo "[5/5] Restarting bench..."
bench restart
echo ""
echo "============================================="
echo " Installation complete!"
echo "============================================="
