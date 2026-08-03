"""Shared test setup.

The UI tests drive a real Textual app; none of them should launch a Tor
process, which would make the suite slow and dependent on the network.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tor2.app import Tor2App


@pytest.fixture(autouse=True)
def no_tor(monkeypatch):
    async def _no_network(self):
        self.set_status("not connected")
    monkeypatch.setattr(Tor2App, "start_network", _no_network, raising=False)


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    """Never read or write the developer's real ~/.config/tor2."""
    from tor2 import store
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setattr(store, "CONFIG_DIR", cfg)
    monkeypatch.setattr(store, "CONFIG_FILE", cfg / "config.json")
    monkeypatch.setattr(store, "CONTACTS_FILE", cfg / "contacts.json")
    monkeypatch.setattr(store, "SERVERS_FILE", cfg / "servers.json")
