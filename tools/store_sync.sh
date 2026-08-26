#!/usr/bin/env bash
#
# Carry the SQLite store between GitHub Actions runs, using a Release asset
# as the backing store.
#
# A fresh runner starts with an empty database, and five upstream feeds hold
# less than a week of history — so an empty store is not a slow start, it is
# permanent data loss for whatever rolled off in between. See docs/design.md "Why
# polling is daily" and docs/persistent-store.md.
#
#   store_sync.sh restore    download the store, verify it, record what we got
#   store_sync.sh save       verify the store, upload it back
#
# The Release is an ordinary prerelease holding one gzipped asset; a year of
# history is ~9 MiB compressed against a 2 GiB per-asset limit. `gh` is
# preinstalled on Actions runners and reads GH_TOKEN.
#
# Environment:
#   NEWSFEED_DB        store path                  (default data/newsfeed.db)
#   STORE_RELEASE_TAG  tag holding the asset       (default store)
#   STORE_BOOTSTRAP    "true" permits an empty start, and nothing else does
#   STORE_STATE_DIR    where the restore receipt lives (default $RUNNER_TEMP)
#   GH_TOKEN           needs contents:write to upload
#
# Both verbs refuse rather than guess. Restore fails when the store is
# missing and STORE_BOOTSTRAP is unset, because an empty database and a quiet
# news week are indistinguishable downstream — the whole hazard this store
# exists to prevent. Save fails when it cannot prove the file it is about to
# upload is a superset of the one it restored.

set -euo pipefail

DB="${NEWSFEED_DB:-data/newsfeed.db}"
TAG="${STORE_RELEASE_TAG:-store}"
ASSET="$(basename "$DB").gz"
STATE_DIR="${STORE_STATE_DIR:-${RUNNER_TEMP:-/tmp}}"
RECEIPT="$STATE_DIR/newsfeed-store-receipt"

die()  { echo "store_sync: $*" >&2; exit 1; }
note() { echo "store_sync: $*"; }

# Item count, or a non-zero exit if the file is not a healthy store. Uses
# python rather than the sqlite3 CLI because the workflow has already set up
# python, and this opens the database exactly as the application does.
inspect() {
  python3 - "$1" <<'PY'
import sqlite3, sys
try:
    conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        sys.exit("integrity_check failed")
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = {"items", "issues", "polls"} - names
    if missing:
        sys.exit(f"not a newsfeed store: missing table(s) {sorted(missing)}")
    print(conn.execute("SELECT count(*) FROM items").fetchone()[0])
except sqlite3.DatabaseError as exc:
    sys.exit(f"unreadable: {exc}")
PY
}

restore() {
  command -v gh >/dev/null || die "gh not found"
  mkdir -p "$(dirname "$DB")" "$STATE_DIR"
  rm -f "$DB" "$DB.gz" "$RECEIPT"

  if gh release download "$TAG" --pattern "$ASSET" --output "$DB.gz" 2>/dev/null; then
    gzip -d -f "$DB.gz" || die "$ASSET downloaded but would not decompress"
    local count
    count="$(inspect "$DB")" || die "restored store is unusable — refusing to run against it.
  The asset in release '$TAG' is corrupt. Do not re-run with STORE_BOOTSTRAP:
  that would publish an empty store over the last good one. Recover the asset
  from the release's history first."
    echo "$count" > "$RECEIPT"
    note "restored $count items, $(du -h "$DB" | cut -f1) from $TAG/$ASSET"
    return
  fi

  # No asset. Either this has never run, or something removed it.
  [ "${STORE_BOOTSTRAP:-}" = "true" ] || die "no '$ASSET' in release '$TAG', and STORE_BOOTSTRAP is not set.

  A scheduled run will not start an empty store on its own: five feeds retain
  under a week, so an empty database looks exactly like a quiet news week and
  would publish as one. If this is genuinely the first run, dispatch the
  workflow manually with bootstrap enabled. If it is not, the asset has gone
  missing and needs recovering before the next poll."

  echo 0 > "$RECEIPT"
  note "BOOTSTRAP: starting an empty store. This is only correct once."
}

save() {
  command -v gh >/dev/null || die "gh not found"
  [ -f "$RECEIPT" ] || die "no restore receipt — restore did not run or did not finish.
  Refusing to upload, because this store may not descend from the stored one."
  [ -f "$DB" ] || die "$DB does not exist; nothing to save"

  local before after
  before="$(cat "$RECEIPT")"
  after="$(inspect "$DB")" || die "$DB is not a healthy store — refusing to upload it"

  # items is insert-only: upsert() is INSERT OR IGNORE and nothing in the
  # codebase deletes rows, so the count cannot legitimately fall. If it has,
  # this file does not descend from the one we restored and uploading it
  # would destroy history.
  [ "$after" -ge "$before" ] || die "store shrank from $before to $after items — refusing to upload.
  Nothing in the pipeline deletes rows, so this file is not a descendant of
  the restored one. Investigate before the next run."

  gzip -9 -c "$DB" > "$DB.gz"

  gh release view "$TAG" >/dev/null 2>&1 || {
    note "creating release '$TAG'"
    gh release create "$TAG" --prerelease --title "Newsfeed store" --notes \
"Backing store for the daily poll — see docs/persistent-store.md.

This is data, not a software release. The asset is the gzipped SQLite
database the scheduled workflows read and write; deleting it loses every
item captured since the last poll that upstream feeds have since dropped."
  }

  gh release upload "$TAG" "$DB.gz" --clobber
  note "saved $after items (+$((after - before))), $(du -h "$DB.gz" | cut -f1) to $TAG/$ASSET"
}

case "${1:-}" in
  restore) restore ;;
  save)    save ;;
  *)       die "usage: store_sync.sh {restore|save}" ;;
esac
