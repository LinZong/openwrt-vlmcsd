#!/bin/sh
set -eu
# APK 3 prints '(N/M) Removing name (version)' (or Purging).
awk '/ (Removing|Purging) / {print $3}' "$1" > "$1.names"
while IFS= read -r name; do
 grep -Fxq "$name" "$2" || { echo "Unapproved removal: $name" >&2; exit 1; }
done < "$1.names"
if grep -Eq ' (Upgrading|Downgrading|Installing) ' "$1"; then echo 'Unexpected package operation' >&2; exit 1; fi
