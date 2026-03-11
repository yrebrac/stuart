---
name: setup
description: Configure Stuart plugin settings — enable/disable tool servers and permissions.
argument-hint: "[list | enable <item> | disable <item> | reset]"
disable-model-invocation: true
allowed-tools:
    - "Bash(python3:*)"
---

Run the Stuart configuration utility to manage which tool servers and permissions are active.

## Dependency check (always run first)

Before anything else, check that the `mcp` Python package is installed:

```
python3 -c "import mcp" 2>/dev/null
```

If this fails (non-zero exit), tell the user:

> Stuart requires the `mcp` Python package. Install it with:
>
> ```
> pip install mcp
> ```
>
> Then restart Claude Code.

Do not proceed with any other setup commands until this passes.

## With arguments

If the user provided arguments (e.g. `/setup list`, `/setup enable container`), run the command directly:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/setup.py $ARGUMENTS
```

Present the output to the user.

## Without arguments (interactive)

If no arguments were provided:

1. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/setup.py list` and present the output
2. Ask the user which items they'd like to enable or disable using AskUserQuestion (multi-select)
3. For each change, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/setup.py enable <item>` or `disable <item>`
4. Tell the user to restart Claude Code for changes to take effect
