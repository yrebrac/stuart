"""
Stuart — Privilege Escalation Module

Central module for polkit-based privilege escalation. Used by MCP
servers to run commands that may need root access.

Architecture:
    - A privilege helper script (stuart-privilege-helper) is installed
      to /usr/local/bin/ and acts as a sub-command whitelist.
    - A polkit rules file (49-stuart.rules) authorizes the helper for
      wheel group members on active local sessions.
    - MCP servers call this module instead of subprocess.run() directly
      for commands that may need root.

Two calling patterns:
    1. Helper-based (preferred, sub-command safe):
       result = priv.run_privileged("smartctl-health", device="/dev/sda")

    2. Raw command with auto-retry:
       result = priv.run_command(["ss", "-tulnp"], privilege="auto")

Fallback chain: unprivileged → pkexec → error with sudo hint.

See docs/TOOL_CONVENTION.md for integration patterns.
See stuart/docs/PRIVILEGES.md for user-facing setup guide.
"""

import hashlib
import shutil
import subprocess
from pathlib import Path


# ── Paths ─────────────────────────────────────────────────────────

HELPER_INSTALLED_PATH = "/usr/local/bin/stuart-privilege-helper"

_MODULE_DIR = Path(__file__).resolve().parent
_POLKIT_DIR = _MODULE_DIR.parent.parent / "config" / "polkit"
HELPER_SHIPPED_PATH = _POLKIT_DIR / "stuart-privilege-helper"
POLICY_SHIPPED_PATH = _POLKIT_DIR / "49-stuart.rules"
POLICY_INSTALLED_PATH = Path("/etc/polkit-1/rules.d/49-stuart.rules")

# Permission error patterns in stderr
_PERMISSION_PATTERNS = (
    "Permission denied",
    "Operation not permitted",
    "EACCES",
    "not permitted",
    "Access denied",
    "must be root",
    "requires root",
    "insufficient privileges",
)


class PrivilegeError(Exception):
    """Raised when privilege escalation fails."""
    pass


class PrivilegeHelper:
    """Manages privilege escalation via polkit + helper script.

    Usage in MCP servers:
        from privilege import PrivilegeHelper

        _priv = PrivilegeHelper()

        # Helper-based (sub-command whitelist):
        result = _priv.run_privileged("smartctl-health", device="/dev/sda")

        # Raw command with auto-retry:
        result = _priv.run_command(["dmesg", "-T"], privilege="auto")

        # Check status:
        status = _priv.policy_status()
    """

    def __init__(self):
        self._pkexec_path: str | None = shutil.which("pkexec")
        self._helper_path: str | None = shutil.which("stuart-privilege-helper")
        if not self._helper_path and Path(HELPER_INSTALLED_PATH).is_file():
            self._helper_path = HELPER_INSTALLED_PATH
        self._status_cache: dict | None = None

    # ── Public API ────────────────────────────────────────────────

    def run_privileged(
        self,
        command_id: str,
        device: str | None = None,
        timeout: int = 30,
    ) -> subprocess.CompletedProcess:
        """Run a command via the privilege helper with pkexec.

        This is the preferred pattern — the helper validates the
        command-id and arguments before execution.

        Args:
            command_id: Helper command ID (e.g. "smartctl-health").
            device: Optional /dev/* device path for commands that need it.
            timeout: Subprocess timeout in seconds.

        Returns:
            CompletedProcess from the helper execution.
            On escalation failure, returns a synthetic CompletedProcess
            with returncode=126 and stderr containing the sudo hint.
        """
        if not self._can_escalate():
            return self._make_failure(
                command_id, device, "Privilege escalation not available"
            )

        cmd = [self._pkexec_path, self._helper_path, command_id]
        if device:
            cmd.append(device)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                cmd, 124,
                stdout="",
                stderr=f"Timed out after {timeout}s running "
                       f"privileged command '{command_id}'.",
            )

        # pkexec returns 126/127 when authorization fails
        if result.returncode in (126, 127) and not result.stdout:
            return self._make_failure(command_id, device, result.stderr)

        return result

    def run_command(
        self,
        cmd: list[str],
        privilege: str = "auto",
        timeout: int = 30,
        helper_command_id: str | None = None,
        helper_device: str | None = None,
    ) -> subprocess.CompletedProcess:
        """Run a raw command with optional privilege escalation.

        Args:
            cmd: Command as list (e.g. ["dmesg", "-T"]).
            privilege: Escalation mode:
                "never"  — just run the command.
                "always" — try pkexec first, fall back to unprivileged.
                "auto"   — try unprivileged, retry with pkexec on
                           permission error.
            timeout: Subprocess timeout in seconds.
            helper_command_id: If provided, use run_privileged() with
                this command ID for escalation instead of raw pkexec.
                This routes through the privilege helper (preferred).
            helper_device: Device path for helper commands that need one.

        Returns:
            CompletedProcess. On permission failure with no escalation
            available, stderr includes sudo hint.
        """
        if privilege == "never":
            return self._run_raw(cmd, timeout)

        if privilege == "always":
            # Try escalation first
            escalated = self._try_escalate(
                cmd, timeout, helper_command_id, helper_device,
            )
            if escalated is not None:
                return escalated
            # Escalation unavailable, try unprivileged
            result = self._run_raw(cmd, timeout)
            if self._is_permission_error(result):
                result.stderr = (
                    result.stderr.rstrip() + "\n\n"
                    + self.format_sudo_hint(cmd)
                )
            return result

        # privilege == "auto": try unprivileged, retry on permission error
        result = self._run_raw(cmd, timeout)
        if self._is_permission_error(result):
            escalated = self._try_escalate(
                cmd, timeout, helper_command_id, helper_device,
            )
            if escalated is not None:
                return escalated
            # Escalation failed, add hint to original error
            result.stderr = (
                result.stderr.rstrip() + "\n\n"
                + self.format_sudo_hint(cmd)
            )

        return result

    def _try_escalate(
        self,
        cmd: list[str],
        timeout: int,
        helper_command_id: str | None,
        helper_device: str | None,
    ) -> subprocess.CompletedProcess | None:
        """Attempt privilege escalation. Returns result or None if unavailable.

        Prefers the helper when a command_id is provided, falls back to
        raw pkexec.
        """
        if not self._can_escalate():
            return None

        # Preferred: use the helper for sub-command safety
        if helper_command_id:
            result = self.run_privileged(
                helper_command_id, device=helper_device, timeout=timeout,
            )
            # 126 = pkexec auth failed — treat as unavailable
            if result.returncode != 126:
                return result

        # Fallback: raw pkexec (for commands not in the helper)
        pkexec_cmd = [self._pkexec_path] + cmd
        try:
            result = subprocess.run(
                pkexec_cmd, capture_output=True, text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return None
        if result.returncode not in (126, 127):
            return result

        return None

    def policy_status(self) -> dict:
        """Check privilege escalation readiness.

        Uses the helper's own check-status command (via pkexec) to read
        installed file hashes as root — eating our own dogfood.

        Returns dict with keys:
            pkexec_available: bool
            polkitd_active: bool
            helper_installed: bool
            policy_installed: bool
            policy_matches_shipped: bool | None (None if can't determine)
            helper_matches_shipped: bool | None (None if can't determine)
            escalation_working: bool
        """
        if self._status_cache is not None:
            return self._status_cache

        status = {
            "pkexec_available": self._pkexec_path is not None,
            "polkitd_active": self._check_polkitd(),
            "helper_installed": self._helper_path is not None,
            "policy_installed": False,
            "policy_matches_shipped": None,
            "helper_matches_shipped": None,
            "escalation_working": False,
        }

        # Use the helper's check-status command via pkexec to get
        # installed file hashes. This proves escalation works AND
        # gives us hashes for comparison — all in one call.
        installed_hashes = {}
        if self._pkexec_path and self._helper_path:
            try:
                result = subprocess.run(
                    [self._pkexec_path, self._helper_path, "check-status"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    status["escalation_working"] = True
                    for line in result.stdout.strip().splitlines():
                        label, _, value = line.partition(":")
                        installed_hashes[label] = value
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        # Policy detection
        if installed_hashes.get("policy") and installed_hashes["policy"] != "missing":
            status["policy_installed"] = True
            shipped_hash = self._file_hash(POLICY_SHIPPED_PATH)
            if shipped_hash is not None:
                status["policy_matches_shipped"] = (
                    installed_hashes["policy"] == shipped_hash
                )

        # Helper comparison
        if installed_hashes.get("helper") and installed_hashes["helper"] != "missing":
            shipped_hash = self._file_hash(HELPER_SHIPPED_PATH)
            if shipped_hash is not None:
                status["helper_matches_shipped"] = (
                    installed_hashes["helper"] == shipped_hash
                )

        self._status_cache = status
        return status

    def format_sudo_hint(self, cmd: list[str]) -> str:
        """Format a sudo fallback hint for when escalation fails.

        Args:
            cmd: The command that needs root.

        Returns:
            User-facing message with the sudo command to run manually.
        """
        cmd_str = " ".join(cmd)
        return (
            f"This command requires elevated privileges.\n"
            f"Run manually: sudo {cmd_str} 2>&1\n"
            f"For automatic escalation, install the Stuart polkit policy.\n"
            f"See: /setup check-privileges"
        )

    @staticmethod
    def is_permission_error(result: subprocess.CompletedProcess) -> bool:
        """Check if a CompletedProcess indicates a permission error.

        Public static version for use by MCP servers that handle
        their own subprocess calls.
        """
        if result.returncode == 0:
            return False
        stderr = (result.stderr or "").lower()
        return any(p.lower() in stderr for p in _PERMISSION_PATTERNS)

    # ── Internal methods ──────────────────────────────────────────

    def _can_escalate(self) -> bool:
        """Check if pkexec + helper are available for escalation."""
        return (
            self._pkexec_path is not None
            and self._helper_path is not None
        )

    def _is_permission_error(self, result: subprocess.CompletedProcess) -> bool:
        """Instance method wrapping the static version."""
        return self.is_permission_error(result)

    @staticmethod
    def _run_raw(
        cmd: list[str], timeout: int,
    ) -> subprocess.CompletedProcess:
        """Run a command without privilege escalation."""
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                cmd, 124, stdout="",
                stderr=f"Timed out after {timeout}s.",
            )
        except FileNotFoundError:
            return subprocess.CompletedProcess(
                cmd, 127, stdout="",
                stderr=f"Command not found: {cmd[0]}",
            )

    @staticmethod
    def _check_polkitd() -> bool:
        """Check if polkitd is active."""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "polkit.service"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() == "active"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def _file_hash(path: Path) -> str | None:
        """SHA256 hash of a file's contents. Returns None if unreadable."""
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except PermissionError:
            return None

    def _make_failure(
        self, command_id: str, device: str | None, detail: str,
    ) -> subprocess.CompletedProcess:
        """Create a synthetic failure result with sudo hint."""
        # Reconstruct the approximate raw command for the sudo hint
        # This is best-effort — the helper knows the exact command
        hint_cmd = [command_id.replace("-", " ")]
        if device:
            hint_cmd.append(device)

        stderr = (
            f"Privilege escalation failed for '{command_id}'.\n"
            f"{detail}\n\n"
            f"This command requires elevated privileges.\n"
            f"For automatic escalation, install the Stuart polkit policy.\n"
            f"See: /setup check-privileges"
        )

        return subprocess.CompletedProcess(
            ["stuart-privilege-helper", command_id], 126,
            stdout="", stderr=stderr,
        )
