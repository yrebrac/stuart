# FAQ

## Contents

- [💬 General](#-general)
- [🐧 Compatibility](#-compatibility)
- [🗣️ Usage](#️-usage)
- [🛡️ Security](#️-security)
- [🔧 Troubleshooting](#-troubleshooting)

---

## 💬 General

**1. What is Stuart?**

Stuart is a Claude Code plugin that transforms Claude into a sysadmin named Stu. Stu has domain skills and 120 purpose-built MCP tools covering Linux and container administration. You talk to Stu; he loads the right domain knowledge, investigates using structured tools, and reports back.

**2. Is it safe?**

Stuart ships with a read-only default posture. MCP tools query system state — they don't modify it. No packages are installed, no services restarted, no configs changed by default. Bash commands (which could modify things) require your explicit approval every time.

That said, Stuart sends system information (logs, process lists, disk info) to Anthropic's API as part of the conversation. Review [Anthropic's privacy policy](https://www.anthropic.com/privacy) and [SECURITY.md](SECURITY.md) for full details.

**3. Is it free?**

Stuart itself is free and open-source (MIT license). However, it requires [Claude Code](https://docs.anthropic.com/en/docs/claude-code), which requires a paid plan — the free Claude.ai plan does not include Claude Code access. Supported plans: Pro, Max, Team, Enterprise, or API (Console). Claude Code also works with third-party providers (Amazon Bedrock, Google Vertex AI, Microsoft Foundry).

Stuart's token usage depends on your queries — complex investigations across multiple domains use more tokens than simple status checks.

**4. Who built this?**

Stuart was built by [Ben Carbery](https://github.com/yrebrac) as a solo project. It was developed and tested using Claude Code with Claude Opus 4.6.

---

## 🐧 Compatibility

**5. Does Stuart work on macOS?**

No. Stuart's MCP servers depend on Linux-specific interfaces — systemd, journald, /proc, /sys, and Linux networking tools. These don't exist on macOS. There are no plans for macOS support, as the target audience and tooling are fundamentally different.

**6. Does Stuart work on Windows / WSL?**

Not currently. WSL2 with systemd enabled may work for some servers, but this hasn't been tested and isn't supported. Native Windows is not supported.

**7. What Linux distributions are supported?**

Any systemd-based distribution. Stuart auto-detects your distro from `/etc/os-release` and selects the appropriate package manager and tool paths. Tested primarily on Fedora, but RPM, DEB, Arch, and SUSE families are all supported.

**8. What Python version do I need?**

Python 3.12 or later. Stuart is developed and tested on Python 3.14.

**9. Will it work on remote machines?**

Not yet. Stuart operates on the local machine only — the machine where Claude Code is running. Remote administration (SSH-based, targeting other hosts) is planned for a future release. For now, you'd need to run Claude Code directly on the machine you want to administer.

**10. Can I use Stuart on a headless server?**

Yes, as long as Claude Code runs on it. Stuart's MCP servers are terminal-based and don't require a desktop environment. The polkit privilege escalation also works on headless systems, though session scoping may require PAM/logind configuration.

**11. Does it work with Docker, Podman, or both?**

Both. Stuart's container server auto-detects the available runtime (Docker, Podman, or both) and uses the appropriate CLI. It supports rootless Podman and Docker socket-based setups.

---

## 🗣️ Usage

**12. How do I talk to Stu?**

Just talk naturally. "Hey Stu, why is my disk filling up?" or "Check my containers" or "What's going on with systemd?" Stu is designed to respond to conversational prompts, not formal command syntax.

**13. How does Stu decide what to investigate?**

Stu follows a 3-step decision flow: (1) answer from knowledge if no tools are needed, (2) make a quick tool call for simple checks, (3) load the relevant domain skill and investigate using MCP tools for anything more involved. For multi-domain questions, he loads multiple skills and synthesises the findings.

**14. Why does Stu load skills?**

Domain skills encode diagnostic workflows, tool selection strategies, and troubleshooting patterns. Without a skill, Stu would guess which tools to use and miss important investigation steps. Loading a skill ensures he follows proven patterns and uses the right MCP tools for the domain.

**15. Can Stu make changes to my system?**

Not through MCP tools — those are read-only. If Stu determines a change is needed (install a package, restart a service, edit a config), he should tell you the command to run yourself, or propose a Bash command that you can approve or reject. You remain in control.

**16. What does `/setup` do?**

`/setup` verifies your Stuart installation: checks MCP server connectivity, confirms the `mcp` Python package is installed, and reports on optional components like polkit privilege escalation. Run it after installing or updating Stuart.

**17. What does `/stutus` do?**

`/stutus` (deliberate spelling — it's a Stu status check) shows a full health report: loaded agents, skills, and MCP server connectivity. Use it to verify everything is working.

**18. Can I use Stuart alongside other Claude Code plugins?**

Yes. Stuart's MCP tools, skills, and agents are namespaced (`stuart:*`) and don't conflict with other plugins. The auto-approval hook only matches Stuart's own tools.

---

## 🛡️ Security

**19. What data does Stuart send to Anthropic?**

The same data any Claude Code session sends — conversation context including tool call outputs. Stuart's MCP tools return system information (log entries, process lists, disk stats, network state), and this data is included in API calls to Anthropic. Stuart does not add any additional data collection.

**20. Can Stuart run commands as root?**

Only if you install the optional polkit helper — see [PRIVILEGES.md](PRIVILEGES.md). Even then, only a specific whitelist of read-only commands can run as root. Without the helper installed, Stuart has no root access.

**21. Can I audit what Stuart's tools do?**

Yes. Every MCP server is a Python script you can read. The polkit helper is a short Python script with a visible command whitelist. The auto-approval hook is a small JSON file. Nothing is obfuscated.

---

## 🔧 Troubleshooting

**22. MCP servers aren't loading**

Run `/setup` and `/stutus` to check connectivity. Common causes:

- **`mcp` package not installed**: Run `pip install mcp`
- **Python version too old**: Stuart requires Python 3.12+
- **Plugin not loaded**: Verify with `/plugin` in Claude Code

**23. Stu doesn't seem to know about my system**

Make sure MCP servers are connected. Run `/stutus` — if servers show as disconnected, Stu can't use their tools and will fall back to generic knowledge. Also try `/profile-system` to build a baseline.

**24. Stu is hallucinating CLI flags**

This happens when Stu falls back to Bash instead of using MCP tools. The MCP tools prevent hallucination by exposing only valid operations, but Bash fallback doesn't have that guardrail. If you see a suspicious command, reject it and ask Stu to use his MCP tools instead.

**25. Stu tried to edit a plugin file**

This shouldn't happen — Stu has a guardrail against modifying plugin files. If it does, reject the edit and remind him: "You're Stu — don't edit plugin files." Please report this as a bug.

**26. Everything is slow**

Specific queries are faster: "What's the status of nginx.service?" can be answered with a single tool call, while "How's my system doing?" requires loading skills and investigating multiple domains. If Stu is spending too long, try narrowing the scope.
