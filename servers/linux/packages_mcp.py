#!/usr/bin/env python3
"""
Stuart — Packages MCP Server

Exposes package management queries as MCP tools for Claude Code.
Detects the Linux distribution and available package managers at startup,
then provides a unified query API regardless of the underlying package
manager (dnf5, dnf4, apt, pacman, zypper). All tools are strictly
read-only — no install, remove, or update operations.

Also provides queries for flatpak and snap packages (if installed),
the alternatives system, and shared library lookups.

Usage:
    python3 packages_mcp.py

Tested on:
    - Fedora 43 (dnf5), Python 3.14

Argument tier decisions (see docs/TOOL_CONVENTION.md):
    Tier 1 (exposed as params):
        package name, search query, pattern, source, count/max_lines,
        enabled_only, security_only, capability, library name
    Tier 2 (param or separate tool):
        source (native/flatpak/snap/all) as param on relevant tools
    Tier 3 (handled internally):
        --quiet, --color=never, -C0, formatting flags, --no-pager
    Tier 4 (omitted):
        install, remove, update, upgrade, --assumeyes, --enablerepo,
        --disablerepo, --downloadonly, --exclude, autoremove, distro-sync,
        swap, mark, group install/remove, module enable/disable/reset

Scope exclusions:
    - Package installation/removal/updates (read-only principle)
    - Repository management (adding, removing, enabling, disabling)
    - Building from source / package creation
    - Language-specific managers (pip, npm, gem, cargo)
    - Container-based isolation (container-specialist domain)
    - AppImage discovery (no central query mechanism)
"""

import json
import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from privilege import PrivilegeHelper
from tool_check import ToolCache

_priv = PrivilegeHelper()

server = FastMCP(
    name="sysops-packages",
    instructions=(
        "Query packages, repositories, and software on this Linux system. "
        "Detects the distribution and package manager automatically. "
        "All tools are read-only — no install/remove/update operations."
    ),
)


# -- Distro detection ─────────────────────────────────────────────

def _detect_distro() -> dict:
    """Read /etc/os-release and return distro information."""
    info = {
        "id": "unknown",
        "name": "Unknown Linux",
        "version": "",
        "version_id": "",
        "version_codename": "",
        "id_like": "",
        "variant": "",
        "variant_id": "",
        "arch": "",
        "family": "unknown",
    }

    os_release = Path("/etc/os-release")
    if os_release.exists():
        try:
            for line in os_release.read_text().splitlines():
                line = line.strip()
                if "=" not in line:
                    continue
                key, _, val = line.partition("=")
                val = val.strip('"').strip("'")
                key_lower = key.lower()
                if key_lower in info:
                    info[key_lower] = val
                elif key == "NAME":
                    info["name"] = val
                elif key == "VERSION":
                    info["version"] = val
                elif key == "VERSION_ID":
                    info["version_id"] = val
                elif key == "VERSION_CODENAME":
                    info["version_codename"] = val
                elif key == "ID":
                    info["id"] = val
                elif key == "ID_LIKE":
                    info["id_like"] = val
                elif key == "VARIANT":
                    info["variant"] = val
                elif key == "VARIANT_ID":
                    info["variant_id"] = val
        except PermissionError:
            pass

    # Detect architecture
    try:
        result = subprocess.run(
            ["uname", "-m"], capture_output=True, text=True, timeout=5
        )
        info["arch"] = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Determine family from ID and ID_LIKE
    distro_id = info["id"].lower()
    id_like = info["id_like"].lower()
    all_ids = f"{distro_id} {id_like}"

    if any(x in all_ids for x in ("fedora", "rhel", "centos", "rocky", "alma")):
        info["family"] = "rpm"
    elif any(x in all_ids for x in ("debian", "ubuntu", "mint", "pop")):
        info["family"] = "deb"
    elif "arch" in all_ids or "manjaro" in all_ids:
        info["family"] = "pacman"
    elif any(x in all_ids for x in ("suse", "opensuse")):
        info["family"] = "zypper"

    return info


# -- Package backend abstraction ──────────────────────────────────

class _PackageBackend(ABC):
    """Abstract base class for distro-specific package manager backends."""

    @abstractmethod
    def name(self) -> str:
        """Return the package manager name (e.g. 'dnf5', 'apt')."""

    @abstractmethod
    def list_repos(self, enabled_only: bool, max_lines: int) -> str:
        """List configured repositories."""

    @abstractmethod
    def list_installed(self, pattern: str, max_lines: int) -> str:
        """List installed packages, optionally filtered by pattern."""

    @abstractmethod
    def search_packages(self, query: str, max_lines: int) -> str:
        """Search available packages."""

    @abstractmethod
    def get_package_info(self, package: str) -> str:
        """Get detailed information about a package."""

    @abstractmethod
    def search_file_owner(self, path: str) -> str:
        """Find which installed package owns a file."""

    @abstractmethod
    def search_provider(self, capability: str) -> str:
        """Find which available packages provide a capability."""

    @abstractmethod
    def list_package_history(self, count: int) -> str:
        """List recent package management actions."""

    @abstractmethod
    def check_updates(self, security_only: bool, max_lines: int) -> str:
        """Check for available updates."""


class _DnfBackend(_PackageBackend):
    """Backend for DNF-based systems (Fedora, RHEL, CentOS, Alma, Rocky).

    Handles both dnf5 (Fedora 41+) and dnf4 (older Fedora, RHEL 8/9).
    Falls back to rpm for queries where dnf output is unreliable.
    """

    def __init__(self):
        self._dnf_path = shutil.which("dnf5") or shutil.which("dnf")
        self._rpm_path = shutil.which("rpm") or "/usr/bin/rpm"
        self._dnf_version = 5  # default assumption
        self._detect_version()

    def _detect_version(self):
        """Detect whether we're running dnf5 or dnf4."""
        if not self._dnf_path:
            return
        try:
            result = subprocess.run(
                [self._dnf_path, "--version"],
                capture_output=True, text=True, timeout=10
            )
            output = result.stdout.lower() + result.stderr.lower()
            if "dnf5" in output or "5." in output.split("\n")[0]:
                self._dnf_version = 5
            else:
                self._dnf_version = 4
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    def name(self) -> str:
        return f"dnf{self._dnf_version}"

    def _run_dnf(self, args: list[str], max_lines: int = 200,
                 timeout: int = 30) -> str:
        if not self._dnf_path:
            return "Error: dnf not found on this system."
        cmd = [self._dnf_path] + args
        return _run_cmd(cmd, max_lines=max_lines, timeout=timeout)

    def _run_rpm(self, args: list[str], max_lines: int = 200,
                 timeout: int = 15) -> str:
        cmd = [self._rpm_path] + args
        return _run_cmd(cmd, max_lines=max_lines, timeout=timeout)

    def list_repos(self, enabled_only: bool, max_lines: int) -> str:
        args = ["repolist"]
        if not enabled_only:
            args.append("--all")
        return self._run_dnf(args, max_lines=max_lines)

    def list_installed(self, pattern: str, max_lines: int) -> str:
        if pattern:
            # rpm -qa is faster for pattern matching on installed packages
            return self._run_rpm(
                ["-qa", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n",
                 pattern + "*"],
                max_lines=max_lines
            )
        return self._run_dnf(
            ["list", "installed"], max_lines=max_lines, timeout=60
        )

    def search_packages(self, query: str, max_lines: int) -> str:
        return self._run_dnf(
            ["search", query], max_lines=max_lines, timeout=30
        )

    def get_package_info(self, package: str) -> str:
        # Try dnf info first (shows both installed and available)
        result = self._run_dnf(["info", package], timeout=15)
        if "Error" not in result and "No matching" not in result:
            return result
        # Fall back to rpm for installed packages
        rpm_result = self._run_rpm(
            ["-qi", package], max_lines=100
        )
        if "not installed" not in rpm_result.lower():
            return rpm_result
        return result  # Return original dnf error

    def search_file_owner(self, path: str) -> str:
        # rpm -qf is fast for installed packages
        return self._run_rpm(["-qf", path])

    def search_provider(self, capability: str) -> str:
        # Fast path: if it looks like a command name (not a glob/path),
        # check if it's already installed via rpm. This avoids dnf
        # metadata refresh which can take >30s on slow mirrors.
        if not capability.startswith("/") and "*" not in capability:
            resolved = shutil.which(capability)
            if resolved:
                local = self._run_rpm(["-qf", resolved])
                if ("not owned" not in local.lower()
                        and not local.startswith("Error")):
                    return (
                        f"[Installed locally]\n{local}\n\n"
                        f"(Found via: {resolved})"
                    )

        # Use repoquery --whatprovides instead of 'provides' because
        # 'provides' needs /usr/lib/sysimage/libdnf5/packages.toml
        # (root-only permissions on some systems), while repoquery
        # does not.
        #
        # For bare command names (no / or *), try both the exact name
        # (matches RPM-level Provides) and a glob prefix (matches
        # file paths in packages).
        queries = [capability]
        if "/" not in capability and "*" not in capability:
            queries.append(f"*/{capability}")

        def _has_results(output: str) -> bool:
            return (not output.startswith("Error")
                    and "(no results)" not in output.lower()
                    and "(no output)" not in output.lower()
                    and output.strip() != "")

        # Try all queries with cached metadata first (fast, no network)
        for query in queries:
            result = self._run_dnf(
                ["repoquery", "-C", "--whatprovides", query],
                max_lines=100, timeout=15,
            )
            if _has_results(result):
                return result

        # Fall back to full metadata refresh with longer timeout
        for query in queries:
            result = self._run_dnf(
                ["repoquery", "--whatprovides", query],
                max_lines=100, timeout=90,
            )
            if _has_results(result):
                return result

        return result

    def list_package_history(self, count: int) -> str:
        result = self._run_dnf(
            ["history", "list", f"--reverse"],
            max_lines=count + 5,  # +5 for header lines
            timeout=15,
        )
        return result

    def check_updates(self, security_only: bool, max_lines: int) -> str:
        # Try privilege helper first for clean root access
        if not security_only:
            result = _priv.run_privileged("dnf-check-update", timeout=60)
            if result.returncode not in (126, 127):
                # Helper worked (0=no updates, 100=updates available)
                output = result.stdout or result.stderr or "(no output)"
                lines = output.strip().split("\n")
                if len(lines) > max_lines:
                    return (
                        f"[Showing first {max_lines} of {len(lines)} "
                        f"lines.]\n\n" + "\n".join(lines[:max_lines])
                    )
                return output.strip() if output.strip() else "(no updates available)"

        # Fallback: direct dnf (may lack root for metadata refresh)
        args = ["check-upgrade" if self._dnf_version >= 5
                else "check-update"]
        if security_only:
            args.append("--security")
        result = self._run_dnf(args, max_lines=max_lines, timeout=60)
        # dnf check-update returns exit code 100 when updates available
        # _run_cmd handles non-zero exit codes already
        return result


# -- Backend selection ────────────────────────────────────────────

_distro = _detect_distro()
_backend: _PackageBackend | None = None

if _distro["family"] == "rpm":
    _backend = _DnfBackend()
# Future: elif _distro["family"] == "deb": _backend = _AptBackend()
# Future: elif _distro["family"] == "pacman": _backend = _PacmanBackend()
# Future: elif _distro["family"] == "zypper": _backend = _ZypperBackend()


# -- ToolCache instances ──────────────────────────────────────────

_tools: dict[str, ToolCache] = {}

# Package manager
if _backend and _backend.name().startswith("dnf"):
    _dnf_path = shutil.which("dnf5") or shutil.which("dnf")
    if _dnf_path:
        _tools["dnf"] = ToolCache(
            "dnf", _dnf_path, ["--version"], ["--help"]
        )
    _rpm_path = shutil.which("rpm")
    if _rpm_path:
        _tools["rpm"] = ToolCache(
            "rpm", _rpm_path, ["--version"], ["--help"]
        )

# Alternatives
_alt_path = shutil.which("alternatives") or shutil.which("update-alternatives")
if _alt_path:
    _alt_name = "alternatives" if "update-alternatives" not in _alt_path \
        else "update-alternatives"
    _tools[_alt_name] = ToolCache(
        _alt_name, _alt_path,
        ["--version"] if _alt_name == "update-alternatives" else [],
        ["--help"],
    )

# Library tools
_ldd_path = shutil.which("ldd")
if _ldd_path:
    _tools["ldd"] = ToolCache("ldd", _ldd_path, ["--version"], ["--help"])

_ldconfig_path = shutil.which("ldconfig")
if _ldconfig_path:
    _tools["ldconfig"] = ToolCache(
        "ldconfig", _ldconfig_path, ["--version"] if False else [],
        ["--help"] if False else [],
        # ldconfig doesn't support --version or --help reliably
    )

# Universal formats
_flatpak_path = shutil.which("flatpak")
_snap_path = shutil.which("snap")

if _flatpak_path:
    _tools["flatpak"] = ToolCache(
        "flatpak", _flatpak_path, ["--version"], ["--help"]
    )
if _snap_path:
    _tools["snap"] = ToolCache(
        "snap", _snap_path, ["--version"], ["--help"]
    )


# -- Shared helpers ───────────────────────────────────────────────

def _run_cmd(
    cmd: list[str],
    max_lines: int = 200,
    timeout: int = 30,
) -> str:
    """Run a command. Returns stdout or error message.

    Output is truncated to max_lines to keep token usage reasonable.
    """
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired:
        return (
            f"Error: command timed out after {timeout} seconds. "
            "This usually means the package manager is refreshing "
            "repository metadata (slow mirror or stale cache). "
            "Alternatives: try search_file_owner() if the tool is "
            "already installed, or use a web search to identify the "
            "package name."
        )
    except FileNotFoundError:
        return f"Error: command not found: {cmd[0]}"

    output = result.stdout or ""
    stderr = (result.stderr or "").strip()

    if result.returncode != 0:
        if "Permission denied" in stderr or "Operation not permitted" in stderr:
            return (
                f"Permission denied running {cmd[0]}.\n\n"
                f"{_priv.format_sudo_hint(cmd)}\n\n"
                f"stderr: {stderr}"
            )
        # dnf check-update returns 100 when updates are available
        if result.returncode == 100 and output.strip():
            pass  # Not an error — updates available
        # rpm -qf returns 1 for "not owned by any package"
        elif result.returncode == 1 and "not owned" in (output + stderr).lower():
            return output.strip() or stderr
        elif result.returncode == 1 and not stderr and not output.strip():
            return "(no results)"
        elif not output.strip():
            return f"Error (exit {result.returncode}): {stderr or '(no output)'}"
        else:
            if stderr:
                output = output.rstrip() + f"\n\n[stderr]: {stderr}"

    if not output.strip():
        return "(no output)"

    lines = output.strip().split("\n")
    if len(lines) > max_lines:
        return (
            f"[Showing first {max_lines} of {len(lines)} lines. "
            f"Narrow your search for full results.]\n\n"
            + "\n".join(lines[:max_lines])
        )
    return output.strip()


def _no_backend_error() -> str:
    """Return error message when no package backend is available."""
    return (
        f"No supported package manager backend for this system.\n"
        f"Detected distro: {_distro['name']} ({_distro['id']})\n"
        f"Family: {_distro['family']}\n\n"
        f"Currently supported: RPM-based (dnf5, dnf4).\n"
        f"Future: apt (Debian/Ubuntu), pacman (Arch), zypper (SUSE)."
    )


# -- Flatpak/Snap helpers ────────────────────────────────────────

def _flatpak_list_installed(max_lines: int = 200) -> str:
    """List installed flatpak applications."""
    if not _flatpak_path:
        return "Flatpak is not installed on this system."
    return _run_cmd(
        [_flatpak_path, "list", "--app", "--columns=application,version,origin"],
        max_lines=max_lines,
    )


def _flatpak_search(query: str, max_lines: int = 50) -> str:
    """Search for flatpak packages."""
    if not _flatpak_path:
        return "Flatpak is not installed on this system."
    return _run_cmd(
        [_flatpak_path, "search", query],
        max_lines=max_lines, timeout=15,
    )


def _flatpak_info(package: str) -> str:
    """Get info about an installed flatpak."""
    if not _flatpak_path:
        return "Flatpak is not installed on this system."
    return _run_cmd([_flatpak_path, "info", package], timeout=10)


def _snap_list_installed(max_lines: int = 200) -> str:
    """List installed snap packages."""
    if not _snap_path:
        return "Snap is not installed on this system."
    return _run_cmd(
        [_snap_path, "list"], max_lines=max_lines,
    )


def _snap_search(query: str, max_lines: int = 50) -> str:
    """Search for snap packages."""
    if not _snap_path:
        return "Snap is not installed on this system."
    return _run_cmd(
        [_snap_path, "find", query],
        max_lines=max_lines, timeout=15,
    )


def _snap_info(package: str) -> str:
    """Get info about a snap package."""
    if not _snap_path:
        return "Snap is not installed on this system."
    return _run_cmd([_snap_path, "info", package], timeout=10)


# -- MCP Tools ────────────────────────────────────────────────────

@server.tool()
def tool_info() -> str:
    """Return distro identification, package manager details, and
    available tools on this system.

    Call this first to understand what distribution, package manager,
    and universal format tools (flatpak, snap) are available.
    """
    result = {
        "distro": {
            k: v for k, v in _distro.items() if v
        },
        "package_manager": {
            "backend": _backend.name() if _backend else "none",
            "supported": _backend is not None,
        },
        "tools": {},
        "universal_formats": {
            "flatpak": _flatpak_path is not None,
            "snap": _snap_path is not None,
        },
    }

    for name, cache in sorted(_tools.items()):
        info = cache.info()
        result["tools"][name] = {
            "exists": info.get("exists", False),
            "path": info.get("path"),
            "version": info.get("version_raw"),
        }

    result["_privilege"] = _priv.policy_status()
    return json.dumps(result, indent=2)


@server.tool()
def read_manual(
    tool: str,
    section: str = "",
) -> str:
    """Read the man page for a package management tool.

    Args:
        tool: Command name, e.g. "dnf", "rpm", "flatpak", "snap",
              "alternatives", "ldd".
        section: Section to extract, e.g. "OPTIONS", "COMMANDS".
                 Leave empty for full page (truncated).
    """
    if tool not in _tools:
        available = ", ".join(sorted(_tools.keys()))
        return f"Unknown tool '{tool}'. Available: {available}"
    return _tools[tool].read_man(section=section)


@server.tool()
def list_repos(
    enabled_only: bool = True,
    max_lines: int = 100,
) -> str:
    """List configured package repositories.

    Args:
        enabled_only: If True (default), show only enabled repos.
                      If False, show all repos including disabled.
        max_lines: Maximum lines to return.
    """
    if not _backend:
        return _no_backend_error()
    return _backend.list_repos(enabled_only, max_lines)


@server.tool()
def list_installed(
    pattern: str = "",
    source: str = "native",
    max_lines: int = 200,
) -> str:
    """List installed packages, optionally filtered by pattern.

    Args:
        pattern: Filter by package name pattern. Supports wildcards.
                 Leave empty for all installed packages.
        source: Package source to query:
                "native" (default) — distro packages (RPM, DEB, etc.)
                "flatpak" — flatpak applications
                "snap" — snap packages
                "all" — all sources combined
        max_lines: Maximum lines to return.
    """
    if source == "flatpak":
        return _flatpak_list_installed(max_lines)
    elif source == "snap":
        return _snap_list_installed(max_lines)
    elif source == "all":
        sections = []
        if _backend:
            sections.append(
                f"## Native packages ({_backend.name()})\n"
                + _backend.list_installed(pattern, max_lines)
            )
        if _flatpak_path:
            sections.append(
                "## Flatpak\n" + _flatpak_list_installed(max_lines)
            )
        if _snap_path:
            sections.append(
                "## Snap\n" + _snap_list_installed(max_lines)
            )
        return "\n\n".join(sections) if sections else _no_backend_error()
    else:  # native
        if not _backend:
            return _no_backend_error()
        return _backend.list_installed(pattern, max_lines)


@server.tool()
def search_packages(
    query: str,
    source: str = "native",
    max_lines: int = 50,
) -> str:
    """Search for available packages by name or description.

    Args:
        query: Search term, e.g. "graphviz", "pdf viewer", "libssl".
        source: Where to search:
                "native" (default) — distro repositories
                "flatpak" — Flathub and configured flatpak remotes
                "snap" — Snap Store
        max_lines: Maximum lines to return.
    """
    if source == "flatpak":
        return _flatpak_search(query, max_lines)
    elif source == "snap":
        return _snap_search(query, max_lines)
    else:  # native
        if not _backend:
            return _no_backend_error()
        return _backend.search_packages(query, max_lines)


@server.tool()
def get_package_info(
    package: str,
    source: str = "native",
) -> str:
    """Get detailed information about a package: description, version,
    repository, dependencies, size, and more.

    Works for both installed and available packages.

    Args:
        package: Package name, e.g. "nginx", "python3", "vim".
                 For flatpak, use the application ID (e.g.
                 "org.mozilla.firefox").
        source: Package source:
                "native" (default) — distro package
                "flatpak" — flatpak application
                "snap" — snap package
    """
    if source == "flatpak":
        return _flatpak_info(package)
    elif source == "snap":
        return _snap_info(package)
    else:  # native
        if not _backend:
            return _no_backend_error()
        return _backend.get_package_info(package)


@server.tool()
def search_file_owner(
    path: str,
) -> str:
    """Find which installed package owns a specific file or command.

    Useful for answering "what package installed this file?" or
    "what package provides this command?".

    Args:
        path: Absolute file path (e.g. "/usr/bin/dot") or command
              name (will be resolved via which).
    """
    if not _backend:
        return _no_backend_error()

    # If not an absolute path, try to resolve via which
    if not path.startswith("/"):
        resolved = shutil.which(path)
        if resolved:
            path = resolved
        else:
            return (
                f"Command '{path}' not found in PATH. "
                f"Try search_provider('{path}') to search available packages, "
                f"or provide the full file path."
            )

    return _backend.search_file_owner(path)


@server.tool()
def search_provider(
    capability: str,
    max_lines: int = 100,
) -> str:
    """Find which available packages provide a file, command, or library.

    Unlike search_file_owner (which checks installed packages only),
    this searches all available packages in enabled repositories.

    Args:
        capability: What to search for. Examples:
                    "/usr/bin/dot" — find packages providing this command
                    "libssl.so" — find packages providing this library
                    "httpd" — find packages providing this name
        max_lines: Maximum lines to return.
    """
    if not _backend:
        return _no_backend_error()
    return _backend.search_provider(capability)


@server.tool()
def list_package_history(
    count: int = 20,
) -> str:
    """List recent package management transactions (installs, updates,
    removals).

    Useful for answering "what changed recently?" or "what did we
    install last week that might have broken X?".

    Args:
        count: Number of recent transactions to show (default 20).
    """
    if not _backend:
        return _no_backend_error()
    return _backend.list_package_history(count)


@server.tool()
def check_updates(
    security_only: bool = False,
    max_lines: int = 100,
) -> str:
    """Check for available package updates.

    Args:
        security_only: If True, show only security-relevant updates.
                       Useful for security audits and kernel patch
                       verification.
        max_lines: Maximum lines to return.
    """
    if not _backend:
        return _no_backend_error()
    return _backend.check_updates(security_only, max_lines)


@server.tool()
def list_alternatives(
    name: str = "",
) -> str:
    """List entries in the alternatives system (update-alternatives /
    alternatives).

    The alternatives system manages symbolic links for commands that
    have multiple provider versions (e.g. python3, java, editor).

    Args:
        name: Alternative group name (e.g. "python3", "java", "editor").
              Leave empty to list all configured groups.
    """
    alt_key = None
    for key in ("alternatives", "update-alternatives"):
        if key in _tools:
            alt_key = key
            break

    if not alt_key:
        return (
            "alternatives/update-alternatives not found on this system. "
            "This tool manages symbolic links for commands with multiple "
            "provider versions."
        )

    alt_path = _tools[alt_key].info().get("path", alt_key)

    if name:
        return _run_cmd(
            [alt_path, "--display", name], max_lines=100
        )
    else:
        return _run_cmd(
            [alt_path, "--list"], max_lines=200
        )


@server.tool()
def check_library(
    name: str,
) -> str:
    """Look up shared library information.

    Can check:
    - What shared libraries a binary needs (ldd)
    - Whether a specific library is available in the system cache
      (ldconfig)

    Args:
        name: Library name (e.g. "libssl.so", "libcurl") or path to
              a binary (e.g. "/usr/bin/python3") to show its library
              dependencies.
    """
    # If it looks like a binary path, use ldd
    if name.startswith("/") or "/" in name:
        if not _ldd_path:
            return "ldd not found on this system."
        if not Path(name).exists():
            return f"File not found: {name}"
        return _run_cmd([_ldd_path, name], max_lines=100)

    # Otherwise, search the ldconfig cache
    if _ldconfig_path:
        result = _run_cmd(
            [_ldconfig_path, "-p"], max_lines=5000, timeout=10
        )
        if result.startswith("Error") or result == "(no output)":
            return result
        # Filter for the requested library
        matches = []
        for line in result.split("\n"):
            if name.lower() in line.lower():
                matches.append(line.strip())
        if matches:
            return (
                f"Libraries matching '{name}' in ldconfig cache:\n"
                + "\n".join(matches[:50])
            )
        return (
            f"No libraries matching '{name}' found in ldconfig cache. "
            f"Try search_provider('{name}') to find packages that "
            f"provide this library."
        )

    return "ldconfig not found on this system."


if __name__ == "__main__":
    server.run(transport="stdio")
