# Changelog

## 1.0.0 — 2026-03-11

- First public release
- Renamed from sysops-agent to stuart
- Restructured plugin: agents, commands, hooks, skills with domain prefixes
- Reorganised servers into linux/ and container/ subdirectories
- Python deps simplified to `pip install mcp` (no venv required)
- Plugin hooks for auto-approving MCP tools and WebFetch
- Self-hosted marketplace support

## 0.9.0 — 2026-03-09

- Skill access for team leader (R.1: skills shared via auto-discovery)
- Performance monitoring MCP server and linux-performance skill (10 tools)
- Stu end-user permissions via plugin PreToolUse hook
- Dev environment permissions plan implemented
- Launch triage: IDEAS restructured, ROADMAP created

## 0.8.0 — 2026-03-08

- Launch plan finalised (Option B: three repos, rsync sync)
- Publish tooling: rsync filter, publish.sh, post-commit hook
- GitHub URL migration (sysops-agent to stuart)

## 0.7.0 — 2026-03-07

- Expanded polkit helper whitelist (ethtool, dmesg, nft, dnf-check-update)
- System profile utility skill and /profile-system command
- NordVPN incident post-mortem: skill safety improvements

## 0.6.0 — 2026-03-05

- Added distro-specialist agent, linux-packages skill and packages MCP server (12 tools)
- Skill/reference split: SKILL.md for workflow, REFERENCE.md for deep knowledge
- Shared stuart-principles skill for cross-agent consistency
- Peer review process established
- Team-leader delegation criteria and on-demand skill loading
- check_reachability tool and outage awareness

## 0.5.0 — 2026-03-04

- Added linux-virtual skill and virtual MCP server (15 tools: KVM/QEMU/libvirt)
- Common Tasks sections added to syslog, systemd, container-runtime, block-device skills
- Normalised tool_info output across MCP servers

## 0.4.0 — 2026-03-03

- Added linux-network skill and network MCP server (16 tools)
- Added syslog MCP server and linux-syslog skill (8 tools)
- Added container-specialist agent, container-runtime skill, container MCP server (17 tools)
- Standardised MCP tool names to verb_noun convention
- Added /setup command, /save-session and /analyse-session commands

## 0.3.0 — 2026-03-02

- Renamed from sysops-agent to stuart
- Restructured plugin with domain-prefixed skills and agents
- Added team-leader skill (Stu persona)
- Added linux-specialist and container-specialist agents

## 0.2.0 — 2026-03-01

- Added serial-device MCP server and linux-serial-device skill (11 tools)
- Added block-device MCP server (10 tools) and linux-block-device skill

## 0.1.0 — 2026-02-28

- Initial plugin with journald and systemd MCP servers
- linux-systemd skill
- Basic plugin manifest and MCP configuration
