#!/usr/bin/env bash

SOURCE_DIR="$HOME/.config/ai/skills"
TARGET_DIR="$HOME/.claude/skills"

echo "🔗 Bridging AI skills to Claude Code..."

# Ensure Claude's skills directory exists
mkdir -p "$TARGET_DIR"

# Find every SKILL.md file in the architecture
find "$SOURCE_DIR" -type f -name "SKILL.md" | while read -r skill_file; do
    
    # Extract the parent directory path and its name
    parent_dir=$(dirname "$skill_file")
    skill_name=$(basename "$parent_dir")
    
    # Symlink the folder into ~/.claude/skills/
    # -s: symbolic, -f: force overwrite, -n: treat destination as normal file
    ln -sfn "$parent_dir" "$TARGET_DIR/$skill_name"
    
    echo "  -> Linked command: /$skill_name"
done

echo "✅ Claude Code is now synced with the AI architecture!"
