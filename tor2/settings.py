"""User settings and the /settings screen.

Defaults are deliberately quiet: nothing makes noise or pops up until you
turn it on.
"""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select, Static, Switch

from . import store

# key -> (label, kind, default, help, choices)
SETTINGS = {
    "notify_sound": ("Notification sound", "bool", False,
                     "Ring the terminal bell on a mention or direct message"),
    "notify_all": ("Sound for every message", "bool", False,
                   "Not just mentions — everything in a channel you're not reading"),
    "desktop_notify": ("Desktop notifications", "bool", False,
                       "Send a system notification (needs notify-send)"),
    "autoplay_gifs": ("Play GIFs automatically", "bool", True,
                      "Otherwise use /play to watch one"),
    "show_previews": ("Inline image previews", "bool", True,
                      "Draw pictures in the chat log"),
    "preview_size": ("Preview size", "choice", "medium",
                     "How large inline pictures are drawn",
                     ["small", "medium", "large"]),
    "show_timestamps": ("Timestamps", "bool", True,
                        "Show the time next to each message"),
    "theme": ("Theme", "choice", "dark",
              "Colour scheme", ["dark", "light", "midnight", "high-contrast"]),
    "compress_quality": ("Video quality", "choice", "balanced",
                         "Higher quality means slower uploads",
                         ["small", "balanced", "high"]),
    "parallel_streams": ("Parallel transfer streams", "choice", "4",
                         "More streams use more Tor circuits — faster, but "
                         "heavier on the network", ["1", "2", "4", "8"]),
    "confirm_quit": ("Confirm before quitting", "bool", False,
                     "Ask before ctrl+q closes everything"),
}

PREVIEW_WIDTHS = {"small": 40, "medium": 60, "large": 90}
CRF = {"small": "32", "balanced": "28", "high": "23"}


def load() -> dict:
    cfg = store.load_config()
    out = {}
    for key, spec in SETTINGS.items():
        out[key] = cfg.get(key, spec[2])
    return out


def get(key: str):
    return load().get(key, SETTINGS[key][2])


def save(key: str, value) -> None:
    cfg = store.load_config()
    cfg[key] = value
    store.save_config(cfg)


class SettingsScreen(ModalScreen):
    """A scrollable list of toggles and choices, like a settings dialog."""

    CSS = """
    SettingsScreen {
        align: center middle;
    }
    #box {
        width: 78;
        max-width: 96%;
        height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #title { text-style: bold; padding-bottom: 1; }
    .row { height: auto; padding: 0 0 1 0; }
    .name { text-style: bold; }
    .help { color: $text-muted; }
    #buttons { height: auto; padding-top: 1; }
    Switch { height: 1; }
    """

    BINDINGS = [("escape", "dismiss_screen", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static("Settings — changes save immediately", id="title")
            with VerticalScroll():
                current = load()
                for key, spec in SETTINGS.items():
                    label, kind, default, help_text = spec[0], spec[1], spec[2], spec[3]
                    with Horizontal(classes="row"):
                        with Vertical():
                            yield Label(label, classes="name")
                            yield Label(help_text, classes="help")
                        if kind == "bool":
                            yield Switch(value=bool(current[key]), id=f"set-{key}")
                        else:
                            choices = spec[4]
                            yield Select([(c, c) for c in choices],
                                         value=str(current[key]),
                                         allow_blank=False, id=f"set-{key}")
            with Horizontal(id="buttons"):
                yield Button("Close", variant="primary", id="close")
                yield Button("Reset to defaults", id="reset")

    def on_switch_changed(self, event: Switch.Changed) -> None:
        key = event.switch.id.removeprefix("set-")
        save(key, bool(event.value))
        self.app.apply_settings()

    def on_select_changed(self, event: Select.Changed) -> None:
        key = event.select.id.removeprefix("set-")
        save(key, str(event.value))
        self.app.apply_settings()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "reset":
            for key, spec in SETTINGS.items():
                save(key, spec[2])
            self.app.apply_settings()
            self.app.pop_screen()
            self.app.sys_msg("settings reset to defaults", "green")
        else:
            self.app.pop_screen()

    def action_dismiss_screen(self) -> None:
        self.app.pop_screen()
