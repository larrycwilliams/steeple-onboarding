#!/bin/zsh
#
# Put the data back. The other half of tools/steeple-backup.sh.
#
#   tools/restore.sh <target-folder> [--from <backup>] [--dry-run] [--force]
#
# The code comes from git; this restores everything git does not carry --
# partners, leads, product_runs, assets, output -- from the nightly mirror,
# then says what is still missing and how to check the result.
#
# Safe by default: it refuses a target that already holds partner records
# unless you pass --force, because the realistic way to lose data during a
# recovery is to restore an old backup over a working app.
#
# Two ways to use it:
#
#   A REHEARSAL, on any Mac, into a scratch folder. Proves the backup is
#   complete and loadable without touching anything live. Do this quarterly.
#
#   A REAL RECOVERY, on a new hub, after cloning the repo. See doc 35 for the
#   whole sequence -- this script is one step of it, not all of it.
set -u

DEFAULT_FROM="$HOME/Library/Mobile Documents/com~apple~CloudDocs/20-Steeple-Stitch/Business-Files/Onboarding-Data-Backup"
TARGET=""
FROM="$DEFAULT_FROM"
DRY=0
FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --from)    FROM="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --force)   FORCE=1; shift ;;
    -*)        echo "unknown option: $1"; exit 2 ;;
    *)         TARGET="$1"; shift ;;
  esac
done

[ -n "$TARGET" ] || { echo "usage: restore.sh <target-folder> [--from <backup>] [--dry-run] [--force]"; exit 2; }

MIRROR="$FROM/current"
[ -d "$MIRROR" ] || { echo "No backup mirror at: $MIRROR"; exit 1; }

echo "Restoring from : $MIRROR"
echo "            to : $TARGET"
echo

if [ -f "$MIRROR/MANIFEST.txt" ]; then
  echo "--- the backup says ---"
  cat "$MIRROR/MANIFEST.txt"
  echo
else
  echo "WARNING: no MANIFEST.txt in the mirror. It was written by backups from"
  echo "         2026-09-15 onwards; an older one is not wrong, just undated."
  echo
fi

# Refuse to restore over a live app unless told twice.
if [ -d "$TARGET/partners" ] && [ "$(ls -1 "$TARGET/partners" 2>/dev/null | wc -l)" -ge 1 ] && [ $FORCE -eq 0 ]; then
  echo "STOP: $TARGET/partners already has records in it."
  echo "      Restoring over a working app is the realistic way to lose data"
  echo "      during a recovery. Pass --force if you are certain."
  exit 1
fi

run() { if [ $DRY -eq 1 ]; then echo "would: $*"; else "$@"; fi }

run mkdir -p "$TARGET"
for d in partners leads product_runs assets output; do
  if [ -d "$MIRROR/$d" ]; then
    run /usr/bin/rsync -a "$MIRROR/$d" "$TARGET/"
  else
    echo "note: $d is not in this backup"
  fi
done

# The ad-hoc safety copies, if the mirror has any.
for extra in "$MIRROR"/partners_backup_*; do
  [ -d "$extra" ] || continue
  run /usr/bin/rsync -a "$extra" "$TARGET/"
done

echo
if [ $DRY -eq 1 ]; then
  echo "Dry run. Nothing was written."
  exit 0
fi

echo "--- what is now in $TARGET ---"
for d in partners leads product_runs assets output; do
  [ -d "$TARGET/$d" ] || continue
  printf '%-14s %6s files %10s\n' "$d" \
    "$(find "$TARGET/$d" -type f | wc -l | tr -d ' ')" \
    "$(du -sh "$TARGET/$d" 2>/dev/null | cut -f1)"
done

echo
echo "--- still missing, by design ---"
if [ -f "$TARGET/.env" ]; then
  echo ".env is present."
else
  echo ".env      secrets never go to iCloud. Recreate it:"
  echo "            SHOPIFY_STORE=steepleandstitch.myshopify.com"
  echo "            SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET from the Shopify"
  echo "              Dev Dashboard app, or your password manager"
  echo "            SHOPIFY_ADMIN_TOKEN is written by the app itself after you"
  echo "              press Connect on Settings — do not hand-copy an old one"
  echo "          chmod 600 .env"
fi
echo "cache/    rebuilds itself on the first Refresh from Shopify."
echo
echo "Then, per doc 35: the venv, LibreOffice, the brand fonts, and the two"
echo "launch agents. Verify with:"
echo "    python tools/disaster_check.py"
echo "    curl -s http://127.0.0.1:5000/ | grep -o 'v3\.[0-9]*'"
