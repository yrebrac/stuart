# Privilege Escalation

How to grant Stuart passwordless access to privileged read-only commands, like those normally requiring `sudo`.

For a broader discussion of Stuart's security posture and agentic AI safety considerations, see [SECURITY.md](SECURITY.md).

## Contents

- [📋 Overview](#-overview)
- [⚡ Quick Setup](#-quick-setup)
- [📜 What the Helper Allows](#-what-the-helper-allows)
- [⚙️ How It Works](#️-how-it-works)
- [🚫 Without Polkit](#-without-polkit)
- [🔧 Customising](#-customising)
- [🛡️ Security](#️-security)
- [🔍 Troubleshooting](#-troubleshooting)

---

## 📋 Overview

Stuart runs with much the same environment and permissions as your user. However, some Linux commands need root to return useful data (e.g. `smartctl` for disk health, `dmidecode` for hardware info, `ss -p` for socket process ownership).

Stuart uses **polkit** for privilege escalation:

- A **privilege helper script** acts as a whitelist of approved commands
- A **polkit rules file** authorizes the helper for admin users
- MCP servers call `pkexec stuart-privilege-helper <command>` when root is needed
- If polkit is not configured, Stuart falls back to clear error messages with `sudo` commands you can run manually

**Stuart works without polkit.** Polkit just removes the friction of running `sudo` commands manually.

---

## ⚡ Quick Setup

Three commands, one-time:

```bash
# From the stuart plugin directory:
sudo cp config/polkit/stuart-privilege-helper /usr/local/bin/
sudo chmod 755 /usr/local/bin/stuart-privilege-helper
sudo cp config/polkit/49-stuart.rules /etc/polkit-1/rules.d/
sudo chmod 644 /etc/polkit-1/rules.d/49-stuart.rules
```

Verify with `/setup check-privileges` in a Stuart session, or from the command line:

```bash
python3 stuart/scripts/setup.py check-privileges
```

### Prerequisites

- **polkit** must be running — check with `systemctl status polkit`. Standard on all modern desktop Linux installs
- Your user must be in the **wheel** group (standard for sysadmins on Fedora/RHEL; `sudo` group on Debian/Ubuntu)
- **pkexec** must be installed (part of the polkit package)

---

## 📜 What the Helper Allows

The helper script (`stuart-privilege-helper`) is the whitelist. Only commands listed in it can run with root privileges:

| Command ID | Runs | Why root is needed |
|---|---|---|
| `smartctl-scan` | `smartctl --scan` | Direct disk access |
| `smartctl-health` | `smartctl -a <device>` | Direct disk access |
| `nvme-list` | `nvme list` | NVMe device enumeration |
| `nvme-smart` | `nvme smart-log <device>` | NVMe health data |
| `blkid-probe` | `blkid -p <device>` | Low-level superblock probing |
| `pvs-list` | `pvs --noheadings` | LVM physical volume listing |
| `vgs-list` | `vgs --noheadings` | LVM volume group listing |
| `lvs-list` | `lvs --noheadings` | LVM logical volume listing |
| `dmidecode-system` | `dmidecode -t system` | BIOS/firmware tables |
| `dmidecode-baseboard` | `dmidecode -t baseboard` | Motherboard info |
| `dmidecode-bios` | `dmidecode -t bios` | BIOS version info |
| `dmidecode-memory` | `dmidecode -t memory` | RAM module details |
| `lsof-device` | `lsof <device>` | Processes using a device |
| `ss-processes` | `ss -tulnp` | Socket process ownership |
| `nft-list` | `nft list ruleset` | Firewall rules (nftables) |
| `ethtool-info` | `ethtool <interface>` | NIC link status and settings |
| `ethtool-driver` | `ethtool -i <interface>` | NIC driver information |
| `dmesg-recent` | `dmesg --since -1h` | Recent kernel messages |
| `dmesg-tail` | `dmesg -T` | All kernel messages |
| `dnf-check-update` | `dnf5 check-update --refresh` | Package update metadata |

Arguments are validated by type:

- **device** — must start with `/dev/`
- **iface** — alphanumeric, hyphens, dots, max 15 characters
- **no argument** — commands with no argument type reject any extra input

The helper also includes **commented write/modify commands** (systemctl, firewall-cmd, dnf) that power users can enable. See the helper script for details.

---

## ⚙️ How It Works

```
Stuart needs root for smartctl:
  -> calls: pkexec stuart-privilege-helper smartctl-health /dev/sda
  -> polkit checks 49-stuart.rules:
      Is user in wheel group?                            yes
      Is session active and local?                       yes
      Is program /usr/local/bin/stuart-privilege-helper?  yes
  -> polkit authorizes (no password prompt)
  -> helper validates: "smartctl-health" is in whitelist,
     /dev/sda starts with /dev/                          yes
  -> helper runs: smartctl -a /dev/sda
  -> output returns to MCP server -> to Stuart -> to you
```

If polkit is not configured, `pkexec` fails immediately (no password prompt, no hang) and Stuart falls back to suggesting the `sudo` command.

---

## 🚫 Without Polkit

Everything still works — tools that need root return clear error messages:

```
Permission denied running smartctl.

This command requires elevated privileges.
Run manually: sudo smartctl -a /dev/sda 2>&1
For automatic escalation, install the Stuart polkit policy.
See: /setup check-privileges
```

---

## 🔧 Customising

### Adding commands

Edit `/usr/local/bin/stuart-privilege-helper` and add entries to the `COMMANDS` dict:

```python
COMMANDS = {
    # ...existing entries...
    "my-custom-cmd": (["my-tool", "--read-only"], False),
    "my-device-cmd": (["my-tool", "--info"],       "device"),
}
```

- `False` = no additional arguments accepted
- `"device"` = accepts one `/dev/*` path as final argument
- `"iface"` = accepts one network interface name as final argument

### Changing the group requirement

Edit `/etc/polkit-1/rules.d/49-stuart.rules` and change `"wheel"` to your preferred group:

```javascript
if (!subject.isInGroup("your-group")) {
```

### Removing commands

Delete or comment out entries in the helper script's `COMMANDS` dict.

---

## 🛡️ Security

**What the shipped polkit policy grants** (you can customise this — see [Customising](#customising)):

- Passwordless execution of specific read-only commands via `pkexec`
- Only for users in the `wheel` group
- Only on active local sessions (logged in directly, not SSH by default)
- Only via the helper script (which validates command IDs and arguments)

**What the policy does NOT grant:**

- No package installation, removal, or updates
- No service management (start, stop, restart)
- No filesystem writes (mount, mkfs, fdisk)
- No arbitrary command execution — only commands in the helper's whitelist
- No network configuration changes
- No access from SSH sessions (unless logind considers them active+local)

**No passwords are stored or transmitted.** The polkit rules file and helper script are plain text — audit them at any time.

---

## 🔍 Troubleshooting

| Problem | Solution |
|---------|----------|
| "Not authorized" from pkexec | Check you're in the `wheel` group: `groups` |
| Policy not taking effect | Restart polkitd: `sudo systemctl restart polkit` |
| Helper "command not found" | Check `/usr/local/bin/stuart-privilege-helper` exists and is executable |
| SELinux denial | Check audit log: `sudo ausearch -m AVC -ts recent` |
| Wrong command paths | Helper uses bare command names resolved via PATH. Check with `which smartctl` |
| SSH session rejected | polkit requires active local session. SSH sessions may not qualify depending on PAM/logind config |
| `/setup check-privileges` says "modified" | You've edited the installed files. This is fine — Stuart just reports it for awareness |
