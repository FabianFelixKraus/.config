#!/usr/bin/env bash
# Download all POSE lecture-slide PDFs from HPI Moodle (the links live in the MOC note),
# then concatenate them — in lecture order — into one big PDF.
#
# AUTH: Moodle pluginfile URLs sit behind your login, so the script needs your
# MoodleSession cookie. Provide it EITHER via the MOODLE_SESSION env var, OR in the
# file ~/.config/moodle/hpi_session (just the raw cookie value, nothing else).
# Get the value from your browser:
#   1. log in to https://moodle.hpi.de
#   2. open DevTools (Cmd+Opt+I) -> Application ▸ Cookies ▸ https://moodle.hpi.de
#   3. copy the value of the "MoodleSession" cookie
# The cookie is short-lived: if downloads come back as "not a PDF", refresh it and re-run.
#
# Usage:
#   bash download_slides.sh                 # download + merge everything in the MOC
#   POSE_SLIDES_DIR=~/some/dir bash download_slides.sh   # custom output folder
# Re-runs are cheap: already-downloaded valid PDFs are skipped.

set -u

VAULT="/Users/fabiankraus/Documents/vault/my_vault"
MOC="$VAULT/Process Oriented Systems Engineering.md"
OUT_DIR="${POSE_SLIDES_DIR:-$HOME/Documents/POSE-slides}"
MERGED="$OUT_DIR/POSE-all-slides.pdf"
COOKIE_FILE="$HOME/.config/moodle/hpi_session"

# --- resolve the session cookie -------------------------------------------------
COOKIE="${MOODLE_SESSION:-}"
if [ -z "$COOKIE" ] && [ -f "$COOKIE_FILE" ]; then
  COOKIE="$(tr -d ' \t\r\n' < "$COOKIE_FILE")"
fi
if [ -z "$COOKIE" ]; then
  echo "ERROR: no MoodleSession cookie found." >&2
  echo "  Set \$MOODLE_SESSION, or write the cookie value to $COOKIE_FILE" >&2
  echo "  (see the comment block at the top of this script for how to get it)." >&2
  exit 1
fi

[ -f "$MOC" ] || { echo "ERROR: MOC not found: $MOC" >&2; exit 1; }
mkdir -p "$OUT_DIR"

# --- collect the PDF URLs in lecture order, de-duped, preserving order ----------
urls="$(grep -oE 'https://moodle\.hpi\.de/[^)]+\.pdf' "$MOC" | awk '!seen[$0]++')"

n=0; ok=0; fail=0
failed_list=""
order_file="$(mktemp)"

echo "Output folder : $OUT_DIR"
echo "Merged file   : $MERGED"
echo "-------------------------------------------------------------"

while IFS= read -r url; do
  [ -z "$url" ] && continue
  n=$((n + 1))
  base="$(basename "$url")"                     # e.g. pose_11.pdf
  idx="$(printf '%02d' "$n")"                   # keep folder + merge order stable
  out="$OUT_DIR/${idx}_${base}"
  echo "$out" >> "$order_file"

  # skip if we already have a valid PDF
  if [ -s "$out" ] && head -c 1024 "$out" | grep -q '%PDF'; then
    printf "  [%s] have  %s\n" "$idx" "${idx}_${base}"
    ok=$((ok + 1)); continue
  fi

  curl -sSL -m 120 --retry 2 --retry-delay 2 \
    -A "Mozilla/5.0 (Macintosh) pose-slide-fetch" \
    -b "MoodleSession=$COOKIE" \
    -o "$out" "$url"

  # a login redirect returns HTML, not a PDF — check the magic bytes
  if [ -s "$out" ] && head -c 1024 "$out" | grep -q '%PDF'; then
    printf "  [%s] ok    %s\n" "$idx" "${idx}_${base}"
    ok=$((ok + 1))
  else
    printf "  [%s] FAIL  %s  (not a PDF — auth expired or file moved)\n" "$idx" "$base"
    rm -f "$out"
    fail=$((fail + 1)); failed_list="${failed_list}  - $url"$'\n'
  fi
  sleep 0.4
done <<EOF
$urls
EOF

echo "-------------------------------------------------------------"
echo "Downloaded $ok/$n PDFs ($fail failed)."

if [ "$fail" -gt 0 ]; then
  echo
  echo "Failed URLs:"; printf "%s" "$failed_list"
  echo "Most likely the MoodleSession cookie is stale — refresh it and re-run"
  echo "(files already downloaded are kept, so it resumes)."
  rm -f "$order_file"
  exit 2
fi

# --- concatenate in lecture order ----------------------------------------------
echo "Merging $ok PDFs -> $MERGED"
files=()
while IFS= read -r f; do [ -f "$f" ] && files+=("$f"); done < "$order_file"
rm -f "$order_file"

if command -v pdfunite >/dev/null 2>&1; then
  pdfunite "${files[@]}" "$MERGED"
elif command -v gs >/dev/null 2>&1; then
  gs -q -dNOPAUSE -dBATCH -sDEVICE=pdfwrite -sOutputFile="$MERGED" "${files[@]}"
else
  echo "ERROR: need 'pdfunite' (brew install poppler) or 'gs' to merge." >&2
  exit 3
fi

pages="$(command -v pdfinfo >/dev/null 2>&1 && pdfinfo "$MERGED" 2>/dev/null | awk '/^Pages:/{print $2}')"
echo "Done -> $MERGED${pages:+  ($pages pages)}"
