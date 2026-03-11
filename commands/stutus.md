---
name: stutus
description: Show Stuart plugin status — loaded skills, available agents, and MCP server health.
allowed-tools:
    - "mcp__plugin_stuart_journald__*"
    - "mcp__plugin_stuart_systemd__*"
    - "mcp__plugin_stuart_block-device__*"
    - "mcp__plugin_stuart_syslog__*"
    - "mcp__plugin_stuart_serial-device__*"
    - "mcp__plugin_stuart_container__*"
---

Report the current Stuart plugin status:

1. List available sub-agents and their status
2. List loaded skills
3. Check MCP server connectivity (call tool_info() on each available server)
4. Summarise any issues found
