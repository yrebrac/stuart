# Security

## Contents

- [🧭 Design Philosophy](#-design-philosophy)
- [🔍 What Stuart Can and Cannot Do](#-what-stuart-can-and-cannot-do)
- [🔐 Permission Model](#-permission-model)
- [🔑 Privilege Escalation](#-privilege-escalation)
- [🌐 Web Fetch](#-web-fetch)
- [📊 Data and Privacy](#-data-and-privacy)
- [⚖️ Risk Assessment](#️-risk-assessment)

---

> **Agents are powerful tools. Use this software at your own risk. Absolutely no warranty is offered.**

---

## 🧭 Design Philosophy

Stuart ships with a **read-only default posture**. Every MCP tool queries system state — none modify it. This is a deliberate defensive choice: in read-only mode, Stuart cannot break your system, install unwanted packages, stop services, or modify your configuration.

Write operations (package installs, service restarts, config edits) are not enabled by default. If you ask Stu to make a change, he should tell you the command to run yourself rather than running it. The polkit privilege helper includes commented-out write commands that power users can enable — see [PRIVILEGES.md](PRIVILEGES.md).

---

## 🔍 What Stuart Can and Cannot Do

### Can do (read-only)

- Query journals, logs, and system messages
- List and inspect systemd units, timers, and dependencies
- Enumerate storage devices, filesystems, mount points, and disk health
- Check network interfaces, routing, DNS, sockets, and connectivity
- List and inspect containers, images, volumes, and compose stacks
- Read package info, repo metadata, and available updates
- Monitor CPU, memory, disk I/O, thermals, and process state
- Enumerate USB, Thunderbolt, and serial devices
- List and inspect KVM/QEMU virtual machines, storage pools, and networks

### Does not do (by default)

Stuart's shipped MCP tools are read-only. The following operations are not exposed through MCP tools, though Stu may suggest manual commands for them:

- Install, remove, or update packages
- Start, stop, restart, or modify services
- Create, mount, format, or resize filesystems
- Modify network configuration, firewall rules, or DNS settings
- Create, start, stop, or delete containers
- Edit configuration files or write to disk
- Execute arbitrary commands as root

Note: Stu can still perform write operations if you approve Bash commands or file edits when prompted. The read-only posture is a default, not a hard limitation.

### Bash fallback

Occasionally Stuart falls back to shell commands via Bash when MCP tools don't cover a specific query. **Every Bash command requires your explicit approval** before execution. You see the exact command and can reject it. This is enforced by Claude Code, not by Stuart — it cannot be bypassed by the plugin.

---

## 🔐 Permission Model

Stuart uses Claude Code's layered permission system:

| Layer | What it does | How |
|-------|-------------|-----|
| **Plugin hook** | Auto-approves Stuart's MCP tools and web fetches | `hooks/hooks.json` — PreToolUse hook matches `mcp__plugin_stuart_.*` and `WebFetch` |
| **Claude Code built-in** | Auto-approves read-only tools | Read, Grep, Glob, WebSearch never prompt |
| **User approval** | Required for Bash and file writes | Claude Code prompts before every Bash command and Edit/Write operation |

The plugin hook is the only permission mechanism Stuart ships. It works by returning `{"decision": "allow"}` for matching tool patterns. You can inspect the hook at `hooks/hooks.json` — it's a small, readable JSON file.

**No `settings.json` permissions are required.** Earlier versions of Stuart required manual permission rules. The hook replaces all of that.

---

## 🔑 Privilege Escalation

Some read-only queries need root access (SMART health, NVMe data, firewall rules, socket process info). Stuart supports optional privilege escalation via polkit:

- A **helper script** (`stuart-privilege-helper`) defines a whitelist of approved commands — nothing outside this list can run as root
- A **polkit rules file** (`49-stuart.rules`) authorizes the helper for users in the `wheel` group on active local sessions
- Arguments are validated by type (`/dev/*` for device paths, alphanumeric for interface names)
- No passwords are stored or transmitted

**Without polkit configured, Stuart still works.** Tools that need root return a clear error message with the exact `sudo` command you can run manually.

For setup and full details: [PRIVILEGES.md](PRIVILEGES.md)

---

## 🌐 Web Fetch

Stuart auto-approves web fetches via its plugin hook. This allows Stu to retrieve documentation, check URLs, and look up external references without prompting.

**Risk assessment:** Web fetches are read-only HTTP requests. They do not modify system state. A compromised or misbehaving model could theoretically encode data in URL query parameters — this risk is low and consistent with Claude Code's own auto-approval of WebSearch. If this concerns you, you can disable the WebFetch auto-approval by editing `hooks/hooks.json` and removing the WebFetch matcher.

---

## 📊 Data and Privacy

- Stuart processes data locally via Claude Code. MCP tool output goes to the Claude API as part of the conversation context — the same as any Claude Code session.
- Stuart does not phone home, collect telemetry, or send data anywhere other than through Claude Code's normal API calls.
- MCP tool output may contain sensitive system information (hostnames, IPs, usernames, service names, log contents). This data is sent to Anthropic's API as part of the conversation. Review [Anthropic's data usage policy](https://www.anthropic.com/privacy) for how conversation data is handled.
- Stuart does not write to disk during normal operation. The `profiles/` directory (for cached system profiles) is the only write target, and it's `.gitignored`.

---

## ⚖️ Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| MCP tools expose system info to Claude API | Low | Same as using Claude Code directly. MCP tools are read-only and scoped. |
| Bash fallback runs unvetted commands | Medium | Every Bash command requires explicit user approval. Review before accepting. |
| Privilege escalation runs root commands | Low | Whitelist-only helper, argument validation, polkit session scoping. User-installed and user-auditable. |
| Web fetch leaks data via URL parameters | Low | Theoretical risk, consistent with Claude Code's WebSearch policy. Disable in hooks if concerned. |
| LLM hallucination suggests wrong commands | Medium | Stu may suggest incorrect manual commands. Always verify before running `sudo` commands from any AI. |
| Plugin hook bypasses tool prompts | Low | By design — only matches Stuart's own read-only MCP tools and WebFetch. Does not affect Bash or Edit/Write. |
