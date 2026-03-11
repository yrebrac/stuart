# Stuart

**Your AI sysops team leader for Linux and container administration.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Claude Code Plugin](https://img.shields.io/badge/Claude_Code-Plugin-blueviolet)](https://github.com/yrebrac/stuart)
[![Version](https://img.shields.io/badge/version-1.0.0-green)](https://github.com/yrebrac/stuart)

Debugging a production issue means juggling journalctl, systemctl, docker logs, smartctl, and dmesg across multiple terminals — all while trying to remember the right flags. Stuart gives you a team that already knows where to look.

Stuart is a [Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugin that transforms Claude into **Stu**, a sysops team leader with specialist [sub-agents](https://docs.anthropic.com/en/docs/claude-code/sub-agents). Stu triages your request, delegates to the right specialist, and presents clear, actionable findings — not raw command dumps.

<!-- TODO: asciinema demo here -->

---

## 🔍 What Stu Actually Does

Ask Stu to check on your system after a reboot:

```
You:  Just rebooted after a big upgrade. How's everything looking?

Stu:  Right, here's the post-reboot summary.

      Overall: Healthy, with one notable issue.

      Failed Mounts (needs attention)

      Three mounts on the Samsung 1TB SSD failed:
      - mnt-data.mount (btrfs)
      - mnt-media.mount (btrfs)
      - mnt-scratch.mount (ext4)

      Is the Samsung drive connected? If it's an external/removable
      drive that isn't plugged in, that's expected. If it should
      be there, we should investigate.

      Storage

      - Root/Home (btrfs over LUKS): 50% — 227G used, 230G free. Comfortable.
      - Boot: 28%, EFI: 12% — both fine.

      Services

      - Ollama: Running, CPU-only mode (no GPU detected)
      - Open-WebUI: Started successfully.
      - No user-session units failed.

      Boot Noise (cosmetic, safe to ignore)

      - KDE portal registration spam — known race condition on Fedora. Harmless.
      - snap-device-helper missing — stale udev rules from upgrade.

      The mount failures are the only thing that needs action.
      Want me to dig into those?
```

Ask about your logging setup:

```
You:  What logs do I have outside journald?

Stu:  Your system runs rsyslog alongside journald, pulling from the
      journal via imjournal. Some flat files are just duplicates of
      what's already in the journal.

      Application-specific logs (NOT in journald):

      | File                    | Program  | Notes              |
      |-------------------------|----------|--------------------|
      | /var/log/snapper.log    | Snapper  | Btrfs snapshots    |
      | /var/log/dnf5.log       | DNF5     | Package history    |
      | /var/log/tuned/tuned.log| TuneD    | Power profiles     |
      | /var/log/firewalld      | Firewall | Config changes     |

      The ones worth paying attention to: Snapper and DNF5 are the
      big ones — snapshot and package history that you won't find
      via journalctl.
```

Stu explains things clearly when asked:

```
You:  Can you explain my firewall setup? Humanise it for me.

Stu:  Here's your firewall setup in plain English:

      Think of zones as different levels of trust. Each network
      interface gets assigned to a zone, and that zone decides what
      traffic is allowed in. Traffic going out is always allowed —
      zones only control what can reach you.

      | Zone    | Interface | What it is                   |
      |---------|-----------|------------------------------|
      | work    | wlp0s20f3 | Your WiFi — main connection  |
      | libvirt | virbr0    | Virtual machine bridge       |
      | public  | (default) | Anything not assigned above  |

      What's NOT allowed: Everything else. If a service or port
      isn't listed, incoming connections are silently dropped.
      That's the right default.
```

And Stu is honest about what he can and can't do:

```
You:  Can you check my firewall rules?

Stu:  No firewall specialist yet, no firewall MCP server, no
      firewall-cmd tooling.

      What we can do today with the existing team:

      | Capability                          | How          |
      |-------------------------------------|--------------|
      | Check if firewalld is running       | systemd MCP  |
      | Read firewalld journal logs         | journald MCP |
      | Spot firewall entries in syslog     | syslog MCP   |

      So we can observe firewalld but not query or manage it.
```

---

## 🧰 Capabilities

Stuart has **120 [MCP](https://docs.anthropic.com/en/docs/claude-code/mcp) tools** across 10 servers, organised into specialist domains:

| Domain | Server | Tools | What it covers |
|--------|--------|-------|----------------|
| **Logs** | journald | 9 | Journal queries by unit, priority, grep, boot, kernel messages |
| **Logs** | syslog | 8 | Flat-file log discovery, searching, rotation tracking |
| **Services** | systemd | 12 | Unit status, dependencies, timers, failed units, unit files |
| **Storage** | block-device | 10 | Devices, filesystems, mounts, SMART health, NVMe, LVM |
| **Network** | network | 16 | Interfaces, routing, DNS, sockets, firewall, WiFi, connectivity |
| **Performance** | performance | 10 | CPU, memory, disk I/O, processes, thermal, PSI pressure |
| **Packages** | packages | 12 | Package search, repos, updates, file ownership, flatpak/snap |
| **Hardware** | serial-device | 11 | USB tree, Thunderbolt, serial ports, device properties |
| **Virtualisation** | virtual | 15 | KVM/QEMU VMs, disk images, storage pools, networks, snapshots |
| **Containers** | container | 17 | Docker/Podman lifecycle, images, volumes, logs, compose stacks |

Three specialist [sub-agents](https://docs.anthropic.com/en/docs/claude-code/sub-agents) handle domain-specific investigations. Stu delegates to them when deeper analysis is needed and presents the results.

**Beyond the tools:** Stu also has the full knowledge of Anthropic's Claude models. The MCP tools give him fast, structured access to your system — but he can reason about Linux, networking, storage, and containers well beyond what the tools cover. If he doesn't have a tool for something, he'll often know the right approach anyway.

---

## 📦 Install

**Prerequisites:** [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (requires a Pro, Max, Team, Enterprise, or API plan), Python 3.12+, Linux with systemd.

### From the marketplace (recommended)

```bash
# In Claude Code:
/plugin marketplace add yrebrac/stuart
/plugin install stuart@stuart
```

### From source

```bash
git clone https://github.com/yrebrac/stuart.git
claude --plugin-dir ./stuart
```

### Then

```bash
pip install mcp    # one-time Python dependency
```

In your Stuart session, run `/setup` to verify MCP servers are connected. Run `/stutus` for a full health check.

See [docs/INSTALL.md](docs/INSTALL.md) for detailed setup, permissions, and tips.

---

## 🐧 Supported Platforms

Stuart is Linux-focused. It is tested primarily on Fedora but supports any systemd-based distribution:

- **RPM**: Fedora, RHEL, CentOS, Alma, Rocky
- **DEB**: Debian, Ubuntu, Mint, Pop!_OS
- **Arch**: Arch, Manjaro, EndeavourOS
- **SUSE**: openSUSE Tumbleweed/Leap

Stuart auto-detects your distro and selects the appropriate package manager and tool paths. Non-systemd distributions may work partially — Stu's model knowledge extends well beyond what the MCP tools cover.

**macOS and Windows are not supported.** Stuart's MCP servers depend on Linux-specific interfaces (systemd, /proc, /sys, journalctl). See the [FAQ](docs/FAQ.md) for more.

---

## 🛡️ Security

Stuart ships with a **read-only default posture**. MCP tools query system state — they don't modify it.

- Stuart's MCP tools are auto-approved via a built-in [plugin hook](https://docs.anthropic.com/en/docs/claude-code/hooks) (no permission config needed)
- Bash commands and file edits still prompt for your approval
- Optional privilege escalation for root-required reads (SMART, NVMe, firewall rules) uses polkit with a whitelist-only helper script — see [docs/PRIVILEGES.md](docs/PRIVILEGES.md)

For full details: [docs/SECURITY.md](docs/SECURITY.md)

---

## ⚠️ Known Limitations

Transparency builds trust. Here's what to expect:

- **Delegation speed**: When Stu delegates to a specialist, round-trips take 30 seconds to several minutes depending on complexity. Stu often handles simple queries directly for speed.
- **Bash fallback**: Occasionally Stu falls back to shell commands when MCP tools don't cover a specific query. You'll be prompted to approve each one.
- **Hallucinated CLI flags**: Like any LLM, Stu can invent flags that don't exist (e.g. `podman pull --dry-run`). The MCP tools prevent this for covered domains, but Bash fallback doesn't have that guardrail.
- **No remote system support**: Stuart operates on the local machine only. Remote/SSH-based administration is planned for a future release.
- **Read-only by default**: Stuart doesn't install packages, restart services, or modify configs out of the box. You can approve individual Bash commands when needed, but the default posture is observe-only.

---

## 💡 Why Not Just Use Claude Code Directly?

You can — Claude Code is already useful for sysadmin tasks. Stuart adds:

- **120 purpose-built MCP tools** that return structured data instead of parsing shell output
- **Domain knowledge** via [skills](https://docs.anthropic.com/en/docs/claude-code/skills) that encode diagnostic workflows, tool selection matrices, and troubleshooting patterns
- **Tuned behaviour**: Stu's responses have been guided and refined for sysops work — he's methodical, clear, opinionated when it matters, and honest about uncertainty
- **A team metaphor** that naturally organises complex investigations across multiple domains
- **Auto-approved tooling** so you're not clicking "Allow" on every query
- **Read-only safety** enforced at the tool level, not just by prompt instructions

Stuart is the difference between "Claude, can you run journalctl and tell me what's wrong?" and having a sysadmin on the team who already knows where to look.

---

## 🗣️ Getting the Most Out of Stu

LLMs respond well to role-play. Treating Stu like a real colleague works.

- **Be conversational.** "Hey Stu, my disk is filling up — can you take a look?" works better than formal prompts.
- **Don't spoonfeed.** Give him the problem, not the steps. He can figure out a lot from context.
- **Redirect when needed.** Like any team member, Stu can go off-track. Stop and redirect him.
- **Give him real problems.** Profile your system, trawl some logs, check your containers. You may be surprised what he finds — and how often you find yourself leaning over to the next cubicle: *"Hey Stu..."*

---

## 🤝 Contributing

Stuart is developed in the open at [yrebrac/stuart-dev](https://github.com/yrebrac/stuart-dev). Issues, feature requests, and pull requests are welcome there.

This repository (`yrebrac/stuart`) is the distribution target — install from here, contribute there.

---

## 📖 More Info

- [docs/INSTALL.md](docs/INSTALL.md) — Detailed setup, permissions, and usage tips
- [docs/SECURITY.md](docs/SECURITY.md) — Security posture, permission model, risk assessment
- [docs/PRIVILEGES.md](docs/PRIVILEGES.md) — Polkit privilege escalation setup
- [docs/FAQ.md](docs/FAQ.md) — Common questions (platform support, pricing, remote machines, etc.)

---

## 📝 License

[MIT](LICENSE)

---

Built and tested using [Claude Code](https://docs.anthropic.com/en/docs/claude-code) with Claude Opus 4.6.
