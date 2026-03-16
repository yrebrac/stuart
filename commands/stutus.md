---
name: stutus
description: Show Stuart plugin status — loaded skills, domain skills, and MCP server health.
allowed-tools:
    - "mcp__plugin_stuart_journald__*"
    - "mcp__plugin_stuart_systemd__*"
    - "mcp__plugin_stuart_block-device__*"
    - "mcp__plugin_stuart_syslog__*"
    - "mcp__plugin_stuart_serial-device__*"
    - "mcp__plugin_stuart_network__*"
    - "mcp__plugin_stuart_packages__*"
    - "mcp__plugin_stuart_virtual__*"
    - "mcp__plugin_stuart_container__*"
---

Report the current Stuart plugin status:

1. List available domain skills
2. List loaded skills
3. Check MCP server connectivity (call tool_info() on each available server)
4. Check privilege escalation status (look for `_privilege` in any tool_info response — report whether escalation is working, partially set up, or not configured)
5. Summarise any issues found — if privileges are not set up or not working, suggest `/setup check-privileges`
