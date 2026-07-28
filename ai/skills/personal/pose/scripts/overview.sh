#!/usr/bin/env bash
# POSE study overview — scans the Obsidian vault for POSE notes and Anki-card coverage.
# POSE notes are identified by the frontmatter tag: lecture: "[[Process Oriented Systems Engineering|POSE]]"
# Usage: bash overview.sh

VAULT="/Users/fabiankraus/Documents/vault/my_vault"
MOC="Process Oriented Systems Engineering.md"

cd "$VAULT" 2>/dev/null || { echo "Vault not found: $VAULT" >&2; exit 1; }

# All POSE notes (by frontmatter tag), excluding the MOC itself and .obsidian internals.
list=$(grep -rlF 'Process Oriented Systems Engineering|POSE' --include='*.md' . 2>/dev/null \
  | grep -v '/.obsidian/' | sed 's|^\./||' | grep -vxF "$MOC" | sort)

total_notes=0
total_cards=0
synced_notes=0
zero_notes=""
rows=""

while IFS= read -r f; do
  [ -z "$f" ] && continue
  c=$(grep -c '#card' "$f" 2>/dev/null); c=${c:-0}
  s=$(grep -c 'ankiID' "$f" 2>/dev/null); s=${s:-0}   # flashcards-obsidian writes <!-- ankiID: ... --> once synced
  total_notes=$((total_notes + 1))
  total_cards=$((total_cards + c))
  [ "$s" -gt 0 ] && synced_notes=$((synced_notes + 1))
  [ "$c" -eq 0 ] && zero_notes="${zero_notes}  - ${f}"$'\n'
  rows="${rows}$(printf '%06d\t%s\t%s' "$c" "$s" "$f")"$'\n'
done <<EOF
$list
EOF

echo "# POSE — Study Overview"
echo
echo "Vault: $VAULT"
echo
printf "%-6s  %-7s  %s\n" "Cards" "Synced" "Note"
printf "%-6s  %-7s  %s\n" "-----" "------" "----"
printf "%s" "$rows" | sort -rn | while IFS=$'\t' read -r c s f; do
  [ -z "$f" ] && continue
  cc=$((10#$c))
  if [ "$s" -gt 0 ]; then sy="yes"; else sy="—"; fi
  printf "%-6s  %-7s  %s\n" "$cc" "$sy" "$f"
done

echo
echo "Totals: ${total_notes} POSE notes · ${total_cards} #card headings · ${synced_notes} notes with synced Anki IDs"

# Rough lecture progress from the MOC "Status Lectures" table (cells marked DONE).
done_l=$(grep -c 'DONE' "$MOC" 2>/dev/null); done_l=${done_l:-0}
echo "MOC lecture rows marked DONE: ${done_l}"

if [ -n "$zero_notes" ]; then
  echo
  echo "## POSE notes with 0 cards (candidates for flashcards):"
  printf "%s" "$zero_notes"
fi
