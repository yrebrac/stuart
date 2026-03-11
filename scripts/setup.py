#!/usr/bin/env python3
"""
Stuart — Plugin Configuration Utility

Reads stuart/config/settings.yaml and manages .claude/settings.json
to control which MCP tool servers and permissions are active.

Can be run via the /setup command in Claude Code or directly from
the shell:

    python3 stuart/scripts/setup.py list
    python3 stuart/scripts/setup.py enable container
    python3 stuart/scripts/setup.py disable WebSearch
    python3 stuart/scripts/setup.py reset
"""

import argparse
import json
import sys
from pathlib import Path

import yaml


# ── Paths ────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR.parent / "config" / "settings.yaml"
PLUGIN_PREFIX = "mcp__plugin_stuart_"


def _settings_path(project_dir: str | None = None) -> Path:
    """Resolve .claude/settings.json relative to project root."""
    base = Path(project_dir) if project_dir else Path.cwd()
    return base / ".claude" / "settings.json"


# ── Config loading ───────────────────────────────────────────────

def load_config() -> dict:
    """Load settings.yaml. Exit with error if missing or malformed."""
    if not CONFIG_PATH.exists():
        print(f"Error: Config not found: {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)
    try:
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"Error: Invalid YAML in {CONFIG_PATH}: {e}", file=sys.stderr)
        sys.exit(1)


def load_settings(path: Path) -> dict:
    """Load existing settings.json, or return empty dict."""
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error: Cannot read {path}: {e}", file=sys.stderr)
        sys.exit(1)


def save_settings(path: Path, settings: dict) -> None:
    """Write settings.json with 4-space indentation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(settings, f, indent=4)
        f.write("\n")


# ── Index building ───────────────────────────────────────────────

def build_index(config: dict) -> dict:
    """Build a flat index of all configurable items.

    Returns: {name: {domain, group, description, default, permission}}
    - domain: "tool-servers" or "permissions"
    - group: specialist name (for tool-servers) or "allow" (for permissions)
    - permission: the settings.json permission string
    """
    index = {}

    for specialist, servers in config.get("tool-servers", {}).items():
        for name, item in servers.items():
            index[name] = {
                "domain": "tool-servers",
                "group": specialist,
                "description": item.get("description", ""),
                "default": item.get("enabled", True),
                "permission": f"{PLUGIN_PREFIX}{name}__*",
            }

    for scope, items in config.get("permissions", {}).items():
        for name, item in items.items():
            index[name] = {
                "domain": "permissions",
                "group": scope,
                "description": item.get("description", ""),
                "default": item.get("enabled", True),
                "permission": name,
            }

    return index


def get_current_state(settings: dict, index: dict) -> dict[str, bool]:
    """Determine enabled/disabled state for each item from settings.json."""
    enabled_servers = set(settings.get("enabledMcpjsonServers", []))
    allowed_perms = set(settings.get("permissions", {}).get("allow", []))

    state = {}
    for name, info in index.items():
        if info["domain"] == "tool-servers":
            state[name] = name in enabled_servers
        else:
            state[name] = info["permission"] in allowed_perms
    return state


# ── Commands ─────────────────────────────────────────────────────

def cmd_list(config: dict, settings_path: Path) -> None:
    """Show all configurable items with current state."""
    index = build_index(config)
    settings = load_settings(settings_path)
    state = get_current_state(settings, index)

    print("Stuart Configuration")
    print("====================")

    # Tool servers grouped by specialist
    current_group = None
    for name, info in index.items():
        if info["domain"] != "tool-servers":
            continue
        if info["group"] != current_group:
            current_group = info["group"]
            print(f"\nTool Servers — {current_group}:")

        tag = "[enabled]" if state.get(name, info["default"]) else "[disabled]"
        print(f"  {name:<14s} {tag:<12s} {info['description']}")

    # Permissions
    perm_items = {n: i for n, i in index.items() if i["domain"] == "permissions"}
    if perm_items:
        print("\nPermissions:")
        for name, info in perm_items.items():
            tag = "[enabled]" if state.get(name, info["default"]) else "[disabled]"
            print(f"  {name:<14s} {tag:<12s} {info['description']}")

    print(f"\nSettings: {settings_path}")


def cmd_enable(config: dict, settings_path: Path, item_name: str) -> None:
    """Enable an item."""
    _set_item(config, settings_path, item_name, enable=True)


def cmd_disable(config: dict, settings_path: Path, item_name: str) -> None:
    """Disable an item."""
    _set_item(config, settings_path, item_name, enable=False)


def _set_item(config: dict, settings_path: Path, item_name: str, enable: bool) -> None:
    """Enable or disable a single item in settings.json."""
    index = build_index(config)

    if item_name not in index:
        valid = ", ".join(sorted(index.keys()))
        print(f"Error: Unknown item '{item_name}'.", file=sys.stderr)
        print(f"Valid items: {valid}", file=sys.stderr)
        sys.exit(1)

    info = index[item_name]
    settings = load_settings(settings_path)
    state = get_current_state(settings, index)
    action = "Enabled" if enable else "Disabled"

    if state.get(item_name) == enable:
        print(f"'{item_name}' is already {action.lower()}.")
        return

    if info["domain"] == "tool-servers":
        _apply_tool_server(settings, index, item_name, enable)
    else:
        _apply_permission(settings, item_name, info["permission"], enable)

    save_settings(settings_path, settings)

    print(f"{action} '{item_name}'.")
    print(f"\nUpdated {settings_path}:")
    if info["domain"] == "tool-servers":
        prefix = "+" if enable else "-"
        print(f"  {prefix} enabledMcpjsonServers: {item_name}")
        print(f"  {prefix} permissions.allow: {info['permission']}")
    else:
        prefix = "+" if enable else "-"
        print(f"  {prefix} permissions.allow: {info['permission']}")
    print("\nRestart Claude Code for changes to take effect.")


def _apply_tool_server(settings: dict, index: dict, name: str, enable: bool) -> None:
    """Update settings.json for a tool-server toggle."""
    settings["enableAllProjectMcpServers"] = False

    # enabledMcpjsonServers
    servers = set(settings.get("enabledMcpjsonServers", []))
    if enable:
        servers.add(name)
    else:
        servers.discard(name)
    settings["enabledMcpjsonServers"] = sorted(servers)

    # permissions.allow — add/remove the MCP permission
    perms = settings.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    perm_str = index[name]["permission"]

    if enable and perm_str not in allow:
        allow.append(perm_str)
    elif not enable and perm_str in allow:
        allow.remove(perm_str)


def _apply_permission(settings: dict, name: str, perm_str: str, enable: bool) -> None:
    """Update settings.json for a permission toggle."""
    perms = settings.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])

    if enable and perm_str not in allow:
        allow.append(perm_str)
    elif not enable and perm_str in allow:
        allow.remove(perm_str)


def cmd_reset(config: dict, settings_path: Path) -> None:
    """Reset all items to their YAML defaults."""
    index = build_index(config)
    settings = load_settings(settings_path)

    settings["enableAllProjectMcpServers"] = False

    # Reset tool-servers
    enabled_servers = []
    perms = settings.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])

    # Remove all stuart MCP permissions first
    allow[:] = [p for p in allow if not p.startswith(PLUGIN_PREFIX)]

    for name, info in index.items():
        if info["domain"] == "tool-servers":
            if info["default"]:
                enabled_servers.append(name)
                allow.append(info["permission"])
        else:
            # Permission items
            if info["default"] and info["permission"] not in allow:
                allow.append(info["permission"])
            elif not info["default"] and info["permission"] in allow:
                allow.remove(info["permission"])

    settings["enabledMcpjsonServers"] = sorted(enabled_servers)

    save_settings(settings_path, settings)

    print("Reset all items to defaults.")
    print(f"\nUpdated {settings_path}:")
    for name, info in index.items():
        tag = "enabled" if info["default"] else "disabled"
        print(f"  {name}: {tag}")
    print("\nRestart Claude Code for changes to take effect.")


# ── Privilege check ──────────────────────────────────────────────

def cmd_check_privileges() -> None:
    """Show privilege escalation status (polkit + helper)."""
    # Import here to avoid requiring privilege.py for other subcommands
    servers_dir = SCRIPT_DIR.parent / "servers" / "linux"
    sys.path.insert(0, str(servers_dir))
    from privilege import PrivilegeHelper

    priv = PrivilegeHelper()
    status = priv.policy_status()

    print("Stuart Privilege Status")
    print("=" * 40)
    print()

    # pkexec
    if status["pkexec_available"]:
        import shutil
        pkexec_path = shutil.which("pkexec") or "pkexec"
        print(f"  pkexec:            installed ({pkexec_path})")
    else:
        print("  pkexec:            not found")

    # polkitd
    if status["polkitd_active"]:
        print("  polkitd:           active")
    else:
        print("  polkitd:           not active")

    # Helper
    if status["helper_installed"]:
        match = status["helper_matches_shipped"]
        if match is True:
            match_str = " (matches shipped)"
        elif match is False:
            match_str = " (MODIFIED)"
        else:
            match_str = ""
        print(f"  privilege helper:  installed{match_str}")
    else:
        print("  privilege helper:  not installed")

    # Policy
    if status["policy_installed"]:
        match = status["policy_matches_shipped"]
        if match is True:
            match_str = " (matches shipped)"
        elif match is False:
            match_str = " (MODIFIED)"
        else:
            match_str = ""
        print(f"  polkit policy:     installed{match_str}")
    else:
        print("  polkit policy:     not installed")

    # Escalation test
    if status["escalation_working"]:
        print("  escalation:        working")
    elif status["helper_installed"] and status["pkexec_available"]:
        print("  escalation:        NOT WORKING (policy missing or denied)")

    print()

    # Install instructions if needed
    polkit_dir = SCRIPT_DIR.parent / "config" / "polkit"
    if not status["helper_installed"] or not status["policy_installed"]:
        print("To install:")
        if not status["helper_installed"]:
            print(f"  sudo cp {polkit_dir}/stuart-privilege-helper"
                  f" /usr/local/bin/")
            print(f"  sudo chmod 755 /usr/local/bin/stuart-privilege-helper")
        if not status["policy_installed"]:
            print(f"  sudo cp {polkit_dir}/49-stuart.rules"
                  f" /etc/polkit-1/rules.d/")
            print(f"  sudo chmod 644 /etc/polkit-1/rules.d/49-stuart.rules")
        print()

    # Command list
    print("Privileged commands (via helper):")
    # Read from the helper script's COMMANDS dict
    helper_path = polkit_dir / "stuart-privilege-helper"
    if helper_path.is_file():
        # Parse COMMANDS from the script
        import ast
        content = helper_path.read_text()
        # Find the COMMANDS dict
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Assign)
                        and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)
                        and node.targets[0].id == "COMMANDS"):
                    commands = ast.literal_eval(node.value)
                    for cmd_id in sorted(commands):
                        base_cmd, arg_type = commands[cmd_id]
                        suffix = " <device>" if arg_type == "device" else ""
                        print(f"  {cmd_id:<24s} "
                              f"{' '.join(base_cmd)}{suffix}")
                    break
        except (SyntaxError, ValueError):
            print("  (could not parse helper script)")
    else:
        print("  (helper script not found)")

    print()
    print("See stuart/docs/PRIVILEGES.md for details.")


# ── CLI ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Stuart plugin configuration utility",
        prog="setup.py",
    )
    parser.add_argument(
        "--project-dir",
        help="Project root directory (default: current directory)",
        default=None,
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="Show current configuration")

    p_enable = sub.add_parser("enable", help="Enable an item")
    p_enable.add_argument("item", help="Item name to enable")

    p_disable = sub.add_parser("disable", help="Disable an item")
    p_disable.add_argument("item", help="Item name to disable")

    sub.add_parser("reset", help="Reset all items to defaults")
    sub.add_parser("check-privileges",
                   help="Show privilege escalation status")

    args = parser.parse_args()

    if args.command == "check-privileges":
        cmd_check_privileges()
        return

    config = load_config()
    settings_path = _settings_path(args.project_dir)

    if args.command == "list":
        cmd_list(config, settings_path)
    elif args.command == "enable":
        cmd_enable(config, settings_path, args.item)
    elif args.command == "disable":
        cmd_disable(config, settings_path, args.item)
    elif args.command == "reset":
        cmd_reset(config, settings_path)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
