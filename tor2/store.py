"""Persistent settings and contacts, stored under ~/.config/tor2/."""

import json
import re
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "tor2"
CONFIG_FILE = CONFIG_DIR / "config.json"
CONTACTS_FILE = CONFIG_DIR / "contacts.json"
SERVERS_FILE = CONFIG_DIR / "servers.json"

NAME_RE = re.compile(r"^[\w\-]{1,32}$")


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(0o700)
    path.write_text(json.dumps(data, indent=2) + "\n")


def load_config() -> dict:
    return _load(CONFIG_FILE)


def save_config(cfg: dict) -> None:
    _save(CONFIG_FILE, cfg)


def load_contacts() -> dict[str, str]:
    return {str(k): str(v) for k, v in _load(CONTACTS_FILE).items()}


def add_contact(name: str, onion: str) -> None:
    if not NAME_RE.match(name):
        raise ValueError("contact names: letters, digits, - and _ only (max 32)")
    contacts = load_contacts()
    contacts[name] = onion
    _save(CONTACTS_FILE, contacts)


def remove_contact(name: str) -> bool:
    contacts = load_contacts()
    if name not in contacts:
        return False
    del contacts[name]
    _save(CONTACTS_FILE, contacts)
    return True


def load_servers() -> dict[str, dict]:
    """{local-name: {'onion': str, 'token': str, 'server': display name}}"""
    return {str(k): v for k, v in _load(SERVERS_FILE).items() if isinstance(v, dict)}


def save_server(name: str, onion: str, token: str, display: str = "") -> None:
    if not NAME_RE.match(name):
        raise ValueError("server names: letters, digits, - and _ only (max 32)")
    servers = load_servers()
    entry = servers.get(name, {})
    entry.update({"onion": onion, "server": display or entry.get("server", name)})
    if token:
        entry["token"] = token
    servers[name] = entry
    _save(SERVERS_FILE, servers)


def remove_server(name: str) -> bool:
    servers = load_servers()
    if name not in servers:
        return False
    del servers[name]
    _save(SERVERS_FILE, servers)
    return True
