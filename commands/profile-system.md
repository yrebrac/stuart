---
name: profile-system
description: View system profile, refresh cached data, or audit Stuart's toolchain.
argument-hint: "[view | refresh [hardware|distro|toolchain] | audit]"
allowed-tools:
    - "mcp__plugin_stuart_journald__*"
    - "mcp__plugin_stuart_systemd__*"
    - "mcp__plugin_stuart_block-device__*"
    - "mcp__plugin_stuart_syslog__*"
    - "mcp__plugin_stuart_serial-device__*"
    - "mcp__plugin_stuart_container__*"
    - "mcp__plugin_stuart_network__*"
    - "mcp__plugin_stuart_virtual__*"
    - "mcp__plugin_stuart_packages__*"
---

Read `system-profile-rules.md` in the `linux-sysadmin` skill directory, then handle the user's request.

## With arguments

### `view` (or no arguments)

1. Read `${CLAUDE_PLUGIN_ROOT}/profiles/hardware.yaml`, `distro.yaml`, and `toolchain.yaml`
2. If profiles exist: present a concise summary table with key facts and profile ages
3. If no profiles exist: tell the user and offer to run `refresh`

### `refresh`

1. Re-gather all three profiles (hardware, distro, toolchain) following the skill's Gathering Strategy
2. Archive any existing profiles first
3. Report what was gathered and any changes from previous profiles

### `refresh hardware` / `refresh distro` / `refresh toolchain`

1. Re-gather only the specified profile
2. Archive the existing file first
3. Report what was gathered

### `audit`

1. Call `tool_info()` on each active MCP server
2. Aggregate results — identify missing tools
3. Present a table: server, tool, status, package, install command
4. Write results to `toolchain.yaml`
