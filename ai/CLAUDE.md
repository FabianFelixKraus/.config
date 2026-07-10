# AI Workspace Architecture

This directory (`~/.config/ai/`) is the central, LLM-agnostic nervous system for all AI interactions, skills, and tools. Do not alter this architecture without explicit permission.

## Directory Structure & Rules

* **`/skills/`**: Contains LLM-agnostic markdown instructions and prompt templates. 
    * `/skills/personal/`: My custom-written skills.
    * `/skills/<author>/`: Third-party skills managed as Git submodules. NEVER modify files in these submodule directories directly.
* **`/tools/`**: Atomic, stateless, executable scripts (e.g., Python, Bash) that perform singular actions (e.g., `read_db_schema.py`). These contain no prompt logic.
* **`/mcp/`**: Configuration files (like `mcp.json`) used to expose the `/tools/` via the Model Context Protocol to any compatible agent.
* **`/bridges/`**: Orchestration scripts that symlink or compile the generic skills/tools into the specific formats required by local CLI tools (e.g., Claude Code, GitHub Copilot).

## Agent Directives
1. When asked to create a new skill, save it as a `SKILL.md` file within a logically named folder inside `/skills/personal/`.
2. When asked to create a tool, ensure it is executable and placed in `/tools/`.
