# Package Management — Reference

Extended reference material for the linux-packages skill. Read this when you need detailed procedures, cross-distro knowledge, or edge case handling.

## Package Format Comparison

When advising on package format choices:

| Factor | Native (RPM/DEB) | Flatpak | Snap | AppImage |
|--------|------------------|---------|------|----------|
| Integration | Full system integration | Good (portal API) | Variable | None |
| Updates | Via system package manager | `flatpak update` | Automatic | Manual |
| Sandboxing | None (trusted) | Strong (Bubblewrap) | Strong (AppArmor) | None |
| Disk usage | Shared libs | Shared runtimes | Self-contained | Self-contained |
| Startup speed | Native | Good | Can be slow (first launch) | Native |
| Governance | Distro maintainers | Flathub (community) | Canonical (closed store) | App developer |

**Default preference:** Native packages from official repos > native from third-party repos > Flatpak > Snap > AppImage. Override based on user needs (e.g. sandboxing, latest version).

## Security & Kernel Updates

- **Kernel updates are security-critical.** Always flag pending kernel updates when checking for updates.
- Use `check_updates(security_only=True)` for focused security audits.
- After kernel updates, recommend a reboot. Services may also need restarting — on dnf-based systems, `sudo dnf needs-restarting` lists affected services.
- For specific CVE inquiries, use WebSearch to find whether a CVE affects installed package versions.
- Note that security updates may lag on some distros — RHEL/Alma patch quickly, Fedora may take longer for non-critical CVEs.

## Troubleshooting Workflows

### "Package not found"

1. `search_packages(query)` — check available packages
2. Check spelling and distro-specific naming conventions
3. `list_repos` — is the right repo enabled? (e.g. EPEL, RPM Fusion, universe)
4. Try `search_packages(source="flatpak")` — available as flatpak?
5. WebSearch for the package + distro name — may need a third-party repo

### "Broken dependency" / "Conflicting packages"

1. `get_package_info(package)` — check what it depends on
2. `list_package_history` — did a recent change cause this?
3. `check_library` — are the required libraries available?
4. Check whether mixing repos (e.g. EPEL + standard) created the conflict
5. Note: resolving conflicts may require package removal or version pinning — recommend actions, don't perform them

### "Missing library" (cannot open shared object file)

1. `check_library("libname.so")` — is it in the ldconfig cache?
2. `search_provider("libname.so")` — which package provides it?
3. `search_file_owner` if you know the path — is the package installed but library not in cache?
4. May need `ldconfig` run (sudo) or library path configuration

### "Why is my X so old?"

1. `get_package_info(package)` — what version is installed?
2. `check_updates` — is an update available in current repos?
3. `list_alternatives(name)` — are multiple versions managed via alternatives?
4. Explain distro's version policy (e.g. Fedora ships latest, RHEL backports security fixes to stable versions)
5. Suggest alternatives: newer repo, flatpak, version manager (e.g. pyenv for Python), or building from source

### "What changed recently that broke X?"

1. `list_package_history(count=50)` — recent transactions
2. Cross-reference timing with when the problem started
3. `get_package_info` on suspicious packages — check version, dependencies
4. Look for library version changes that may affect dependent software

## Cross-Distro Equivalence

When users ask "how do I do X on this distro?" — translate between package managers:

| Operation | DNF (Fedora/RHEL) | APT (Debian/Ubuntu) | Pacman (Arch) |
|-----------|-------------------|---------------------|---------------|
| Update repo metadata | `dnf check-update` | `apt update` | `pacman -Sy` |
| Upgrade all packages | `dnf upgrade` | `apt upgrade` | `pacman -Syu` |
| Install a package | `dnf install pkg` | `apt install pkg` | `pacman -S pkg` |
| Remove a package | `dnf remove pkg` | `apt remove pkg` | `pacman -R pkg` |
| Search for a package | `dnf search term` | `apt search term` | `pacman -Ss term` |
| Package info | `dnf info pkg` | `apt show pkg` | `pacman -Si pkg` |
| What owns a file | `rpm -qf /path` | `dpkg -S /path` | `pacman -Qo /path` |
| List installed | `dnf list installed` | `dpkg -l` | `pacman -Q` |
| History/log | `dnf history` | `/var/log/apt/history.log` | `/var/log/pacman.log` |

Note: this is reference knowledge. Use it to advise users, not to run commands on distros you don't have tools for.

## Version Management

**Alternatives system:** `alternatives` (RHEL/Fedora) or `update-alternatives` (Debian/Ubuntu) manages symbolic links for commands with multiple providers.

Common scenarios:
- Multiple Python versions: `python3` → python3.11 or python3.12
- Multiple Java versions: `java` → java-17 or java-21
- Editor defaults: `editor` → vim, nano, etc.

**Warning:** Do not recommend creating custom alternatives for system-critical commands like `python3` on systems that use Python for system tools — this can break dnf, yum, and other system utilities. Recommend `pyenv` or similar version managers for user-space version switching instead.

**DNF Modules / AppStream** (RHEL/Fedora): Some packages offer module streams for version selection (e.g. `nodejs:18`, `python:3.12`). Check with `dnf module list` if available.

## Known Quirks

- **dnf metadata refresh timeouts:** `dnf provides`, `dnf search`, and `dnf check-upgrade` may trigger a metadata refresh if the cache is stale. On slow mirrors this can take 30–90+ seconds, causing tool timeouts. The `search_provider` tool mitigates this by trying local rpm first, then cached metadata (`-C`), then full refresh. If all three fail, fall back to WebSearch rather than retrying. Common trigger: running a package query for the first time in hours/days.
- **dnf5 vs dnf4 output:** dnf5 (Fedora 41+) has cleaner, more structured output. dnf4 (RHEL 8/9, older Fedora) output may need different parsing. The MCP server handles this internally, but be aware if output looks different from expected.
- **`rpm -qf` vs `dnf provides`:** `rpm -qf` only checks installed packages (fast, local). `dnf provides` searches all available packages (slower, needs metadata). Prefer `search_file_owner` when the tool might already be installed.
- **Flatpak runtime sharing:** Flatpak apps share runtimes (e.g. org.freedesktop.Platform). Installing one flatpak may pull a large runtime; subsequent flatpaks using the same runtime are much smaller.
- **Snap loopback mounts:** Each snap creates a loopback mount visible in `mount` output. This is normal and not a concern.
- **EPEL vs base conflicts:** EPEL packages occasionally conflict with base RHEL packages. Check before recommending EPEL packages on RHEL systems.
- **`dnf check-update` exit code:** Returns exit code 100 (not 0) when updates are available. This is not an error.
