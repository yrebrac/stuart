"""
Stuart — Tool Discovery & Cache Utility

Shared utility for MCP servers. Provides tool existence checking,
version capture, help text caching, and man page reading.

Not an MCP server itself — imported by each server.

See docs/TOOL_CONVENTION.md for the full convention.
"""

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "stuart" / "tools"
CACHE_MAX_AGE_DAYS = 30

# Additional paths to search for tools beyond the inherited PATH.
# MCP server processes often inherit a restricted PATH that excludes
# sbin directories where many admin tools live (especially on RHEL-family).
_EXTRA_SEARCH_PATHS = [
    "/sbin",
    "/usr/sbin",
    "/usr/local/sbin",
    "/usr/local/bin",
]


class ToolCache:
    """Discovers and caches metadata for a wrapped Linux command.

    Usage:
        _cache = ToolCache(
            tool_name="journalctl",
            tool_path="/usr/bin/journalctl",
            version_args=["--version"],
            help_args=["--help"],
        )

        # In an MCP tool:
        @server.tool()
        def tool_info() -> str:
            return _cache.info_json()
    """

    def __init__(
        self,
        tool_name: str,
        tool_path: str,
        version_args: list[str] | None = None,
        help_args: list[str] | None = None,
        man_name: str | None = None,
    ):
        """
        Args:
            tool_name: Cache key, e.g. "journalctl".
            tool_path: Absolute path or command name, e.g. "/usr/bin/journalctl".
            version_args: Args to get version, e.g. ["--version"]. None to skip.
            help_args: Args to get help text, e.g. ["--help"]. None to skip.
            man_name: Man page name if different from tool_name, e.g. "systemd-journalctl".
        """
        self.tool_name = tool_name
        self.tool_path = tool_path
        self.version_args = version_args or ["--version"]
        self.help_args = help_args or ["--help"]
        self.man_name = man_name or tool_name
        self._cache_file = CACHE_DIR / f"{tool_name}.json"
        self._data: dict | None = None

        # Auto-discover on init if cache is missing or stale
        loaded = self._load()
        if loaded is None or loaded.get("stale", True):
            self._data = self.discover()
        else:
            self._data = loaded

    @staticmethod
    def _which_extended(name: str) -> str | None:
        """Find a command, searching the inherited PATH plus _EXTRA_SEARCH_PATHS.

        shutil.which() only searches os.environ["PATH"]. On many distros
        (especially RHEL-family), admin tools live in /sbin/ or /usr/sbin/
        which are not on PATH for non-root users or non-login shells.
        """
        # Try the normal PATH first
        resolved = shutil.which(name)
        if resolved:
            return resolved

        # Search additional paths
        for extra_dir in _EXTRA_SEARCH_PATHS:
            candidate = os.path.join(extra_dir, os.path.basename(name))
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate

        return None

    def discover(self) -> dict:
        """Run discovery checks and update cache. Returns the cache dict."""
        resolved = self._which_extended(self.tool_path)
        exists = resolved is not None

        if not exists:
            data = {
                "exists": False,
                "path": None,
                "version_raw": None,
                "version_captured": None,
                "help_text": None,
                "help_captured": None,
            }
            self._save(data)
            self._data = data
            return data

        today = datetime.now().strftime("%Y-%m-%d")

        # Capture version
        version_raw = self._run_quiet([resolved] + self.version_args)

        # Capture help text
        help_text = self._run_quiet([resolved] + self.help_args)

        data = {
            "exists": True,
            "path": resolved,
            "version_raw": version_raw,
            "version_captured": today,
            "help_text": help_text,
            "help_captured": today,
        }
        self._save(data)
        self._data = data
        return data

    def info(self) -> dict:
        """Return cached tool info, refreshing if stale."""
        if self._data is None or self._data.get("stale", False):
            return self.discover()
        return self._data

    def info_json(self) -> str:
        """Return cached tool info as formatted JSON string (for MCP tools)."""
        return json.dumps(self.info(), indent=2)

    def read_man(self, section: str = "", max_lines: int = 200) -> str:
        """Read the man page, optionally extracting a named section.

        Args:
            section: Section heading to extract, e.g. "OPTIONS", "DESCRIPTION",
                     "EXIT STATUS". Case-insensitive. Empty for full page.
            max_lines: Truncate output to this many lines.

        Returns:
            Man page text, or error message if unavailable.
        """
        try:
            env = {**os.environ, "COLUMNS": "100", "MANWIDTH": "100"}
            result = subprocess.run(
                ["man", "--pager=cat", self.man_name],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return f"Error: man {self.man_name} timed out."
        except FileNotFoundError:
            return "Error: 'man' command not found."

        if result.returncode != 0:
            return f"No man page found for '{self.man_name}'."

        # Strip backspace-based formatting (bold/underline from man)
        text = re.sub(r".\x08", "", result.stdout)

        if section:
            # Try exact match first, then substring match
            extracted = self._extract_section(text, section)
            if extracted is None:
                extracted = self._extract_sections_containing(text, section)
            if extracted is None:
                sections = re.findall(r"^([A-Z][A-Z /&-]+)$", text, re.MULTILINE)
                available = ", ".join(s.strip() for s in sections) if sections else "unknown"
                return (
                    f"Section '{section}' not found.\n"
                    f"Available sections: {available}"
                )
            text = extracted

        lines = text.strip().split("\n")
        if len(lines) > max_lines:
            return (
                f"[Showing first {max_lines} of {len(lines)} lines]\n\n"
                + "\n".join(lines[:max_lines])
            )
        return text.strip()

    # --- internal methods ---

    def _load(self) -> dict | None:
        """Load cache from file. Returns None if missing. Adds 'stale' flag."""
        if not self._cache_file.exists():
            return None

        try:
            data = json.loads(self._cache_file.read_text())
        except (json.JSONDecodeError, OSError):
            return None

        # Check staleness
        captured = data.get("version_captured")
        if captured:
            try:
                captured_date = datetime.strptime(captured, "%Y-%m-%d")
                age = datetime.now() - captured_date
                data["stale"] = age > timedelta(days=CACHE_MAX_AGE_DAYS)
            except ValueError:
                data["stale"] = True
        else:
            data["stale"] = True

        return data

    def _save(self, data: dict) -> None:
        """Write cache to file. Strips transient keys like 'stale'."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        clean = {k: v for k, v in data.items() if k != "stale"}
        self._cache_file.write_text(json.dumps(clean, indent=4) + "\n")

    def _run_quiet(self, cmd: list[str]) -> str | None:
        """Run a command, return stdout (or stderr). None on failure."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            output = result.stdout.strip() or result.stderr.strip()
            return output if output else None
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None

    @staticmethod
    def _extract_section(text: str, section: str) -> str | None:
        """Extract a single exactly-named section from man page text.

        Sections start with an all-caps heading at the left margin and
        end when the next such heading appears.
        """
        lines = text.split("\n")
        in_section = False
        section_lines = []

        for line in lines:
            if re.match(r"^[A-Z][A-Z /&-]+$", line.strip()):
                if in_section:
                    break
                if line.strip().upper() == section.upper():
                    in_section = True
                    section_lines.append(line)
                continue

            if in_section:
                section_lines.append(line)

        return "\n".join(section_lines) if section_lines else None

    @staticmethod
    def _extract_sections_containing(text: str, keyword: str) -> str | None:
        """Extract all sections whose headings contain the keyword.

        Useful when a man page splits OPTIONS into "SOURCE OPTIONS",
        "FILTERING OPTIONS", etc. Asking for "OPTIONS" returns all of them.
        """
        lines = text.split("\n")
        keyword_upper = keyword.upper()
        in_section = False
        result_lines = []

        for line in lines:
            if re.match(r"^[A-Z][A-Z /&-]+$", line.strip()):
                heading = line.strip().upper()
                if keyword_upper in heading:
                    in_section = True
                    result_lines.append(line)
                elif in_section:
                    in_section = False
                continue

            if in_section:
                result_lines.append(line)

        return "\n".join(result_lines) if result_lines else None
