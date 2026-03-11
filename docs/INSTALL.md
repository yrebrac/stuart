# Install

## Contents

- [🖥️ Claude Code](#️-claude-code)
- [🐍 Python Setup](#-python-setup)
- [📦 Install the Plugin](#-install-the-plugin)
- [🚀 First Run](#-first-run)
- [🔐 Permissions](#-permissions)
- [⚡ Reducing Bash Prompts](#-reducing-bash-prompts)
- [🔑 Optional: Privilege Escalation](#-optional-privilege-escalation)
- [🗣️ Getting the Most Out of Stu](#️-getting-the-most-out-of-stu)
- [🔄 Updating](#-updating)
- [🗑️ Uninstalling](#️-uninstalling)

---

## 🖥️ Claude Code

Stuart is a plugin for **Claude Code**, Anthropic's CLI agent. You need Claude Code installed and authenticated before installing Stuart.

- **Install Claude Code:** https://docs.anthropic.com/en/docs/claude-code
- **Requires:** a Claude Pro or Max subscription, or an Anthropic API key
- **Platform:** Linux with systemd (Fedora, Ubuntu, Debian, Arch, RHEL, etc.)

The installer may appear to hang for several minutes while downloading — this is normal.

---

## 🐍 Python Setup

Stuart's MCP servers require **Python 3.12+** and the MCP Python SDK:

```bash
pip install mcp
```

This is the only pip dependency. Everything else is standard Python and Linux utilities.

If you prefer isolation, a virtual environment works too:

```bash
python3 -m venv ~/.local/share/stuart-venv
~/.local/share/stuart-venv/bin/pip install mcp
```

But you'll need to ensure the venv's Python is on your PATH when Claude Code starts the MCP servers, so global installation is simpler.

---

## 📦 Install the Plugin

### Option A: Marketplace (recommended)

```bash
# In a Claude Code session:
/plugin marketplace add yrebrac/stuart
/plugin install stuart@stuart
```

### Option B: Git clone

Requires **git**.

```bash
git clone https://github.com/yrebrac/stuart.git
claude --plugin-dir ./stuart
```

The `--plugin-dir` flag loads Stuart for that session only. The marketplace method persists across sessions.

---

## 🚀 First Run

1. Start a session with Stuart installed (marketplace) or loaded (`--plugin-dir`)
2. Run `/setup` — this verifies MCP servers can start and checks for the `mcp` package
3. Run `/stutus` — this shows a full health check: agents, skills, and MCP server connectivity

If `/setup` reports issues, it will tell you exactly what to fix.

---

## 🔐 Permissions

Stuart's plugin automatically approves its own MCP tool calls and web fetches via a built-in hook. **No permission configuration is required.**

### What never prompts

| Tool type | Why |
|-----------|-----|
| Stuart's MCP tools | Auto-approved by plugin hook (read-only tools) |
| Web fetches | Auto-approved by plugin hook |
| Web searches, file reads, grep, glob | Auto-approved by Claude Code |

### What still prompts

| Tool type | Why |
|-----------|-----|
| Bash commands | Stuart occasionally falls back to shell commands. You approve each one. This is the correct security posture for a tool with system access. |
| File edits/writes | Stuart is read-only by default. If you ask him to save output to a file, you'll be prompted. |

**Tip:** If you notice Stu using a Bash command when you know he has an MCP tool for it, interrupt him and guide him back: *"Hey Stu, don't you have a tool for checking that?"* This helps him stay on the MCP path, which is faster and avoids hallucinated CLI flags.

---

## ⚡ Reducing Bash Prompts

If you find Bash prompts excessive, you can pre-approve common read-only commands. Add these to your `~/.claude/settings.json`:

```json
{
    "permissions": {
        "allow": [
            "Bash(uname *)",
            "Bash(cat /etc/*)",
            "Bash(ip *)",
            "Bash(ss *)",
            "Bash(df *)",
            "Bash(free *)",
            "Bash(uptime)",
            "Bash(hostname *)",
            "Bash(lsblk *)",
            "Bash(mount)"
        ]
    }
}
```

**Note:** Compound commands (e.g. `uname -a && cat /etc/os-release`) always prompt regardless of allow rules. This is a Claude Code limitation, not a Stuart issue.

**Caution:** Only pre-approve commands you're comfortable running without review. Stuart's MCP tools aim to cover most common queries — Bash fallback is the exception, not the rule, but users will encounter queries that go beyond MCP coverage.

---

## 🔑 Optional: Privilege Escalation

Some system queries need root (disk SMART data, NVMe health, firewall rules, socket process info). Stuart handles this gracefully:

- **Without polkit setup**: Stuart tells you exactly what `sudo` command to run manually
- **With polkit setup**: Stuart escalates automatically via a whitelist-only helper script — no passwords, no arbitrary commands

To set up automatic escalation, see [PRIVILEGES.md](PRIVILEGES.md).

---

## 🗣️ Getting the Most Out of Stu

LLMs respond well to role-play. Treating Stu like a real colleague works.

- **Be conversational.** "Hey Stu, my disk is filling up" works better than formal prompts.
- **Don't spoonfeed.** Give him the problem, not the steps. He can figure out a lot from context.
- **Redirect when needed.** Like any team member, Stu can go off-track. Stop and redirect him.
- **Set him to work on real problems.** Profile your system, trawl some logs, check your containers. You may be surprised what he finds.

---

## 🔄 Updating

### Marketplace install

```bash
# In Claude Code:
/plugin update stuart@stuart
```

### Git clone

```bash
cd /path/to/stuart
git pull
```

---

## 🗑️ Uninstalling

### Marketplace install

```bash
# In Claude Code:
/plugin uninstall stuart@stuart
/plugin marketplace remove stuart
```

### Git clone

Delete the cloned directory. If you installed the polkit helper, also remove:

```bash
sudo rm /usr/local/bin/stuart-privilege-helper
sudo rm /etc/polkit-1/rules.d/49-stuart.rules
```

### Python dependency

```bash
pip uninstall mcp
```
