#!/bin/zsh
#
# Nightly backup of everything the hub holds that git does not.
#
# Lives in the repo on purpose. It used to live only in ~/bin, which is in
# neither git nor the backup -- the one thing protecting everything else was
# the one thing with no protection at all. The launch agent points here now.
#
# What it copies, and why each is here:
#
#   partners/      the records. Contacts, terms, signers, and underneath them
#                  _history/, _sent/, _agreement/ and _people.json.
#   leads/         the pipeline and its stages.
#   product_runs/  the Traveler's work.
#   assets/        partner logos and artwork. IRREPLACEABLE -- these came from
#                  the churches themselves, and losing them means emailing
#                  eleven organisations to ask for their logo again. Added
#                  2026-09-15 after tools/disaster_check.py found 77 MB of
#                  them protected by nothing at all.
#   output/        generated agreements, statements, kits, postcards. Added the
#                  same day, against the earlier belief that they were
#                  regenerable. They are regenerable from TODAY's record,
#                  templates and terms.json -- which means a document already
#                  signed by a partner cannot be reproduced after a price
#                  change. A sent document is a record, not an artifact.
#
# Deliberately NOT copied:
#
#   .env           secrets stay out of iCloud. Recovery recreates it; doc 35
#                  says from where. This is a decision, not an oversight.
#   cache/         Shopify data that rebuilds itself on the next refresh.
#
# Run it by hand any time. --dry-run shows what it would do and writes nothing.
set -u

SRC="$HOME/Dev/steeple-onboarding"
DEST="$HOME/Library/Mobile Documents/com~apple~CloudDocs/20-Steeple-Stitch/Business-Files/Onboarding-Data-Backup"
LOG="$HOME/Library/Logs/steeple-backup.log"
STAMP=$(date '+%Y-%m-%d_%H%M')
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

# The `if` is not decoration. Written as `[ $DRY -eq 1 ] && echo "$*"`, this
# function returns 1 on every real run -- and since the last line of the script
# is a call to it, the whole backup then exits 1 having done everything
# perfectly. Caught by a test run, which is the only reason it is not sitting
# in the log tonight claiming failure.
log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
  if [ $DRY -eq 1 ]; then echo "$*"; fi
}
run() { if [ $DRY -eq 1 ]; then echo "would: $*"; else "$@"; fi }

# Refuse to run against a source that looks wrong. An empty or missing
# source must never be allowed to overwrite a good backup.
for d in partners leads product_runs assets; do
  [ -d "$SRC/$d" ] || { log "ABORT: $SRC/$d missing"; exit 1; }
done
[ "$(ls -1 "$SRC/partners" | wc -l)" -ge 1 ] || { log "ABORT: partners/ empty"; exit 1; }
[ "$(ls -1 "$SRC/assets" | wc -l)" -ge 1 ] || { log "ABORT: assets/ empty"; exit 1; }

run mkdir -p "$DEST/current" "$DEST/snapshots"

# Mirror. Deliberately NO --delete: a wiped source must not wipe the backup.
run /usr/bin/rsync -a --exclude '.env' \
  "$SRC/partners" "$SRC/leads" "$SRC/product_runs" "$SRC/assets" "$SRC/output" \
  "$DEST/current/" 2>>"$LOG"

# Ad-hoc safety copies somebody made by hand before a risky change. They are
# small, they are evidence, and they were protected by nothing.
# Written the long way rather than with a zsh glob qualifier, so the syntax
# can be checked with bash -n as well as zsh -n. A backup script nobody can
# lint is a backup script nobody checks.
for extra in "$SRC"/partners_backup_*; do
  [ -d "$extra" ] || continue
  run /usr/bin/rsync -a "$extra" "$DEST/current/" 2>>"$LOG"
done

# Dated snapshot, so corruption is recoverable rather than faithfully copied.
# Records only: they are small, they change daily, and they are the ones where
# a bad edit is the likely disaster. assets/ and output/ are mirrored but not
# snapshotted -- fourteen copies of 200 MB to protect files that almost never
# change in place would cost 3 GB to solve a problem that is not the one we
# have.
run /usr/bin/tar -czf "$DEST/snapshots/steeple-data_$STAMP.tar.gz" \
  -C "$SRC" partners leads product_runs 2>>"$LOG"

# Keep the last 14.
if [ $DRY -eq 0 ]; then
  ls -1t "$DEST/snapshots"/steeple-data_*.tar.gz 2>/dev/null | tail -n +15 | while read -r f; do rm -f "$f"; done
fi

# A manifest, so whoever restores knows what they are holding and how old it
# is -- without that, a restore starts by guessing whether the backup ran.
#
# Nothing in here may fail the run. The copying is already done by this point,
# and a backup that reports failure because it could not describe itself would
# be the second tool this project has met that lies about its own outcome.
if [ $DRY -eq 0 ]; then
  {
    echo "Steeple & Stitch onboarding data backup"
    echo "taken:     $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo "from:      $(scutil --get ComputerName 2>/dev/null || hostname)"
    echo "app:       $(grep -m1 'APP_VERSION = ' "$SRC/app.py" 2>/dev/null | cut -d'"' -f2 || true)"
    echo "commit:    $(cd "$SRC" 2>/dev/null && git rev-parse --short HEAD 2>/dev/null || true)"
    echo
    for d in partners leads product_runs assets output; do
      printf '%-14s %6s files %10s\n' "$d" \
        "$(find "$DEST/current/$d" -type f 2>/dev/null | wc -l | tr -d ' ')" \
        "$(du -sh "$DEST/current/$d" 2>/dev/null | cut -f1)"
    done
    echo
    echo "NOT in here: .env (secrets stay out of iCloud -- see doc 35)"
    echo "             cache/ (rebuilds itself)"
  } > "$DEST/current/MANIFEST.txt"
fi

COUNT=$(find "$DEST/current" -type f 2>/dev/null | wc -l | tr -d ' ')
SIZE=$(du -sh "$DEST/current" 2>/dev/null | cut -f1)
log "ok — mirrored and snapshotted ($STAMP) — $COUNT files, $SIZE"
