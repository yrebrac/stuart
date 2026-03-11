---
name: linux-packages
description: >
    Domain knowledge for package management using the packages MCP server.
    Load BEFORE using any packages MCP tool directly. Covers distro-aware
    package queries, repository management, dependency analysis, alternatives,
    shared libraries, and universal formats.
---

# Package Management

## Session Start

Before investigating package issues, establish what you're working with:

1. Call `tool_info()` to detect the distro, package manager, and available tools
2. Note the distro family (rpm, deb, pacman, zypper) and package manager version
3. Note whether flatpak and snap are available
4. If the backend is unsupported, note the limitation — you can still advise using domain knowledge and WebSearch

## Common Tasks

### "Any updates?"

1. `check_updates()` — get the full list
2. If many updates: group them (kernel, desktop environment, libraries, other)
3. Flag security-critical items: kernel, glibc, openssl, systemd
4. Summarise: "47 updates — 3 security, kernel 6.x.y → 6.x.z, KDE Plasma 6.6 accounts for 28"
5. Offer the update command (e.g. `sudo dnf upgrade`) or ask if user wants details

### "Install X"

1. `search_packages(query)` — find candidates
2. `get_package_info(package)` — check deps, size, repo source
3. If not in native repos: check flatpak, then suggest third-party repos with trust caveats
4. Recommend with install command and source attribution

### "What changed / what broke?"

1. `list_package_history(count=30)` — recent transactions
2. Cross-reference timing with when the problem started
3. Investigate suspicious packages with `get_package_info`

### "What package provides command X?"

1. `search_file_owner("X")` — fast, checks installed packages via rpm (no network)
2. If not installed: `search_provider("X")` — searches all available packages (may need metadata refresh)
3. If both time out or fail: use WebSearch for "what package provides X on [distro]"

The `search_provider` tool tries local resolution first, then cached metadata, then full refresh. But on slow mirrors or stale caches, even the full refresh may time out. Don't retry the same tool — fall back to WebSearch.

### "Is X installed?"

1. `list_installed(pattern="X")` — direct check
2. If not found, `search_packages(query)` to find available options

For detailed troubleshooting procedures and cross-distro equivalence, read REFERENCE.md in this skill directory.

## Tool Selection

| Goal | Tool |
|------|------|
| What distro/package manager? | `tool_info` |
| What repos are configured? | `list_repos` |
| Is package X installed? | `list_installed(pattern="X")` |
| What's installed from flatpak? | `list_installed(source="flatpak")` |
| Everything installed? | `list_installed(source="all")` |
| Find a package to install | `search_packages(query)` |
| Package details (version, deps, size) | `get_package_info(package)` |
| Which package owns this file? | `search_file_owner(path)` |
| What provides this file/command? | `search_provider(capability)` |
| What changed recently? | `list_package_history` |
| Are there updates available? | `check_updates` |
| Security updates only? | `check_updates(security_only=True)` |
| Python/Java version alternatives? | `list_alternatives(name)` |
| What libraries does a binary need? | `check_library("/path/to/binary")` |
| Is a library available? | `check_library("libname")` |
| Package manager manual | `read_manual(tool, section)` |

## Query Strategy

### Scope first, then broaden

1. Start with the specific package name or pattern
2. If not found in native repos, try `search_provider` for file/command lookups
3. If still not found, try `search_packages(source="flatpak")` or use WebSearch to locate third-party repos
4. **Be suspicious of empty results.** Check for typos, try alternative package names (e.g. `python3-devel` vs `python3-dev` vs `python-devel`), try without version suffixes.

### Package name conventions by distro

- **RPM-based:** `python3-requests`, `python3-devel`, `kernel-headers`, `gcc-c++`
- **DEB-based:** `python3-requests`, `python3-dev`, `linux-headers-generic`, `g++`
- When a user asks for a package by a name from a different distro, translate to the local convention.

### Efficient queries

- Use `list_installed(pattern="...")` with wildcards for broad searches rather than listing everything
- Use `get_package_info` before `search_provider` — it's faster and shows installed state
- Use `search_file_owner` for "what installed this?" (only installed packages)
- Use `search_provider` for "what provides this?" (all available packages)

## Preferences & Safety

- **Prefer native packages** from official repos over universal formats
- **Check dependencies** before recommending a package — note if it pulls in many deps
- **Warn about third-party repos** — note trust and maintenance implications
- **Package installation requires root** — mention this in recommendations. For read-only queries like checking updates, Stuart auto-escalates via polkit when configured.
- **Version mixing is dangerous** — warn when different sources provide different versions of the same library
- **Check for conflicts** — if a recommendation might conflict with existing packages, say so
- **Kernel updates are security-critical** — always flag pending kernel updates
