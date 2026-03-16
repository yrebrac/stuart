---
name: linux-packages
description: >
    Domain knowledge for package management using the packages MCP server.
    Load BEFORE using any packages MCP tool directly. Covers distro-aware
    package queries, repository management, dependency analysis, alternatives,
    shared libraries, and universal formats.
---

# Package Management

## Guide

This file covers distro-aware package management — queries, repos, updates, dependencies.

- **Domain Model** — distro families, package managers, universal formats
- **Heuristics** — expert shortcuts for package troubleshooting
- **Anti-patterns** — common mistakes with package management
- **Procedures** — workflows for updates, installs, broken deps, missing libraries
- **Tools** — goal-to-tool lookup for the packages MCP server
- **Query Strategy** — scope-first, naming conventions, efficient queries
- **Safety** — privilege, version mixing, third-party repo warnings, cross-domain pointers
- **Quirks** — dnf metadata timeouts, dnf5 vs dnf4, flatpak runtimes, snap loopbacks
- **Domain Deep Knowledge** — cross-distro equivalence, version management, format comparison (inline)

## Domain Model

**Distro families:**
- **RPM-based:** Fedora → RHEL → CentOS/Alma/Rocky (dnf5/dnf4/yum)
- **Debian-based:** Debian → Ubuntu → Mint/Pop!_OS (apt/dpkg)
- **Arch-based:** Arch → Manjaro/EndeavourOS (pacman)
- **SUSE:** openSUSE Tumbleweed/Leap → SLES (zypper)

**Universal formats:** Flatpak (Flathub), Snap (Snapcraft/Canonical), AppImage. Supplement, not replace, native packages.

**Release models:** Rolling (Fedora, Arch, Tumbleweed) vs point-release (RHEL, Ubuntu LTS). Affects update frequency and version stability.

**Default preference:** Native packages from official repos > native from third-party repos > Flatpak > Snap > AppImage. Override based on user needs.

## Heuristics

1. "Package not found" is usually a naming convention difference between distros. `python3-devel` (RPM) vs `python3-dev` (DEB). Translate first, then search.
2. `search_file_owner` is fast (local rpm query). `search_provider` is slow (may refresh metadata). Try owner first for "what provides this?".
3. When `search_provider` times out, don't retry — fall back to WebSearch. dnf metadata refresh on stale caches can take 90+ seconds.
4. Kernel updates are always security-critical. Always flag them when reporting available updates.
5. If many updates are pending, group them (kernel, desktop environment, libraries, other) for a clear summary.

## Anti-patterns

- Don't recommend packages that create dependency conflicts without checking first. `get_package_info` shows dependencies.
- Don't mix package versions from different sources (e.g. EPEL + base) without warning about conflict risk.
- Don't recommend creating custom alternatives for `python3` on systems that use Python for system tools — this breaks dnf/yum. Recommend `pyenv` instead.
- Don't assume a package name works across distros — `gcc-c++` (RPM) vs `g++` (DEB), `kernel-headers` vs `linux-headers-generic`.
- Don't retry `search_provider` after a timeout — fall back to WebSearch.

## Procedures

### Update check and summary
When user asks about available updates.

1. `check_updates()` — full list
2. IF many updates:
     Group: kernel, desktop environment, libraries, other
     Flag security-critical: kernel, glibc, openssl, systemd
3. Summarise: "47 updates — 3 security, kernel 6.x.y → 6.x.z, KDE Plasma 6.6 accounts for 28"
4. Offer update command (e.g. `sudo dnf upgrade`) or ask if user wants details
5. VERIFY: User understands what's pending and security implications

### Package installation
When user wants to install something.

1. `search_packages(query)` — find candidates
2. `get_package_info(package)` — check deps, size, repo source
3. IF not in native repos:
     `search_packages(source="flatpak")` — available as flatpak?
     If not: suggest third-party repos with trust caveats
4. IF many dependencies: note the count and any large pulls
5. Recommend with install command and source attribution
6. VERIFY: Package is installable without conflicts

### Package not found
When a package search returns nothing.

1. `search_packages(query)` — try available packages
2. Check spelling and distro-specific naming conventions (see Query Strategy)
3. `list_repos` — is the right repo enabled? (EPEL, RPM Fusion, universe)
4. `search_packages(source="flatpak")` — available as flatpak?
5. IF still not found: WebSearch for the package + distro name
6. VERIFY: Found the package or determined it's not available for this distro

### What provides this file/command?
When user needs to find which package provides a command or file.

1. `search_file_owner("X")` — fast, checks installed packages (rpm -qf)
2. IF not installed:
     `search_provider("X")` — searches all available packages
3. IF timeout: WebSearch "what package provides X on [distro]"
4. VERIFY: Identified the providing package

### Broken dependency / conflict
When a package operation fails due to dependency issues.

1. `get_package_info(package)` — what it depends on
2. `list_package_history` — did a recent change cause this?
3. `check_library` — are required libraries available?
4. Check whether mixing repos created the conflict
5. Note: resolving may require removal or version pinning — recommend, don't perform
6. VERIFY: Dependency tree is resolvable

### Missing library
When a binary reports "cannot open shared object file."

1. `check_library("libname.so")` — in the ldconfig cache?
2. `search_provider("libname.so")` — which package provides it?
3. `search_file_owner` if you know the path — installed but not in cache?
4. May need `ldconfig` (sudo) or library path configuration
5. VERIFY: Library found in ldconfig cache after fix

### Recent change investigation
When something broke and user suspects a package change.

1. `list_package_history(count=50)` — recent transactions
2. Cross-reference timing with when the problem started
3. `get_package_info` on suspicious packages — version, deps
4. Look for library version changes affecting dependent software
5. VERIFY: Identified the change that caused the issue
6. CROSS-DOMAIN: If a service broke after update → `linux-systemd-rules.md` "Service failure investigation"

## Tools

| Goal | Tool |
|------|------|
| What distro/package manager? | `tool_info` |
| What repos are configured? | `list_repos` |
| Is package X installed? | `list_installed(pattern="X")` |
| Flatpak packages? | `list_installed(source="flatpak")` |
| Find a package to install | `search_packages(query)` |
| Package details (version, deps) | `get_package_info(package)` |
| Which package owns this file? | `search_file_owner(path)` |
| What provides this command? | `search_provider(capability)` |
| Recent package changes? | `list_package_history` |
| Updates available? | `check_updates` |
| Security updates only? | `check_updates(security_only=True)` |
| Version alternatives? | `list_alternatives(name)` |
| Library availability? | `check_library` |
| Package manager manual | `read_manual(tool, section)` |

## Query Strategy

1. Start with the specific package name or pattern. If not found, try `search_provider`. Then flatpak. Then WebSearch.
2. Be suspicious of empty results — check for typos, alternative names (`python3-devel` vs `python3-dev`), without version suffixes.
3. Package name conventions by distro:
   - **RPM-based:** `python3-requests`, `python3-devel`, `kernel-headers`, `gcc-c++`
   - **DEB-based:** `python3-requests`, `python3-dev`, `linux-headers-generic`, `g++`
   When user uses a name from a different distro, translate.
4. Use `list_installed(pattern="...")` with wildcards for broad searches.
5. Use `get_package_info` before `search_provider` — it's faster and shows installed state.
6. Use `search_file_owner` for "what installed this?" (only installed packages). Use `search_provider` for "what provides this?" (all available).

## Safety

### Privilege

Package installation/removal requires root. Read-only queries (check_updates, list_installed, search) auto-escalate via polkit when configured.

### High-risk operations

- **Package removal**: May break reverse dependencies. Check before recommending.
- **Version mixing**: Different versions of the same library from different sources can break the system.
- **Third-party repos**: Note trust and maintenance implications (EPEL is well-maintained; random COPR may not be).
- **Kernel updates**: Security-critical. Always flag. Recommend reboot after. On dnf systems: `sudo dnf needs-restarting`.

### Cross-references

- If a service broke after update → `linux-systemd-rules.md` "Service failure investigation"
- If a missing library affects a running service → `linux-systemd-rules.md` for restart
- If package uses disk space → `linux-block-device-rules.md` "Disk full investigation"
- If flatpak/snap has networking issues → `linux-network-rules.md`

## Quirks

- **dnf metadata refresh timeouts**: `search_provider` mitigates by trying local rpm first, then cached, then full refresh. If all fail, use WebSearch.
- **dnf5 vs dnf4 output**: dnf5 (Fedora 41+) has cleaner output. MCP server handles differences internally.
- **`rpm -qf` vs `dnf provides`**: `rpm -qf` = installed only (fast). `dnf provides` = all available (slow).
- **Flatpak runtime sharing**: First flatpak may pull a large runtime; subsequent apps using same runtime are smaller.
- **Snap loopback mounts**: Each snap creates a loopback mount in `mount` output. Normal.
- **EPEL vs base conflicts**: EPEL packages occasionally conflict with base RHEL packages.
- **`dnf check-update` exit code**: Returns 100 (not 0) when updates available. Not an error.

## Domain Deep Knowledge

### Cross-distro equivalence

| Operation | DNF (Fedora/RHEL) | APT (Debian/Ubuntu) | Pacman (Arch) |
|-----------|-------------------|---------------------|---------------|
| Update metadata | `dnf check-update` | `apt update` | `pacman -Sy` |
| Upgrade all | `dnf upgrade` | `apt upgrade` | `pacman -Syu` |
| Install | `dnf install pkg` | `apt install pkg` | `pacman -S pkg` |
| Remove | `dnf remove pkg` | `apt remove pkg` | `pacman -R pkg` |
| Search | `dnf search term` | `apt search term` | `pacman -Ss term` |
| Info | `dnf info pkg` | `apt show pkg` | `pacman -Si pkg` |
| File owner | `rpm -qf /path` | `dpkg -S /path` | `pacman -Qo /path` |
| List installed | `dnf list installed` | `dpkg -l` | `pacman -Q` |
| History | `dnf history` | `/var/log/apt/history.log` | `/var/log/pacman.log` |

### Version management

**Alternatives system:** `alternatives` (RHEL/Fedora) / `update-alternatives` (Debian/Ubuntu) manages symlinks for commands with multiple providers. Common: Python, Java, editor defaults.

**DNF Modules/AppStream** (RHEL/Fedora): Module streams for version selection (e.g. `nodejs:18`, `python:3.12`). Check with `dnf module list`.

### Package format comparison

| Factor | Native (RPM/DEB) | Flatpak | Snap | AppImage |
|--------|------------------|---------|------|----------|
| Integration | Full | Good (portal API) | Variable | None |
| Updates | System pkg manager | `flatpak update` | Automatic | Manual |
| Sandboxing | None (trusted) | Strong (Bubblewrap) | Strong (AppArmor) | None |
| Disk usage | Shared libs | Shared runtimes | Self-contained | Self-contained |
| Governance | Distro maintainers | Flathub (community) | Canonical (closed) | Developer |
