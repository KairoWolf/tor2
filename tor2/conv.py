"""Conversations: one live connection each, several at a time.

The UI shows one conversation at a time, but every one of them keeps running
in the background. Message handlers are shared between foreground and
background conversations, so rather than threading a "which conversation is
this?" argument through all of them, each receive loop marks its conversation
in a :class:`~contextvars.ContextVar`. Because asyncio tasks each carry their
own context, a handler awaiting inside one task never sees another task's
conversation.
"""

import contextvars

from rich.text import Text
from textual.widgets import Static

# Set by each receive loop; falls back to whatever the user is looking at.
current: contextvars.ContextVar["Conversation | None"] = contextvars.ContextVar(
    "tor2_current_conversation", default=None)

MAX_LINES = 2000


class Conversation:
    """One connection: a direct chat with a person, or a server."""

    def __init__(self, key: str, kind: str, title: str, session=None):
        self.key = key                  # stable id used by the sidebar
        self.kind = kind                # "dm" | "server"
        self.title = title
        self.session = session
        self.state = "idle"
        self.peer_nick = "peer"
        self.srv: dict = {}
        self.lines: list = []           # rendered log for this conversation
        self.unread = 0
        self.mentioned = False
        self.incoming_video = None
        self.last_rx = 0.0
        self.manual_disconnect = False

    # ---------- log ----------

    def append(self, renderable) -> None:
        self.lines.append(renderable)
        if len(self.lines) > MAX_LINES:
            del self.lines[:len(self.lines) - MAX_LINES]

    @property
    def label(self) -> str:
        return self.title[:16]

    def sidebar_row(self, active: bool) -> Text:
        t = Text()
        icon = "▣" if self.kind == "server" else "▸"
        t.append(f"{icon} {self.label}")
        if not active and self.mentioned:
            t.append("  @", style="bold red")
        elif not active and self.unread:
            t.append(f"  {self.unread if self.unread < 100 else '99+'}",
                     style="bold yellow")
        return t


class LogProxy:
    """Stands in for the chat widget: writes land in the conversation that is
    currently being handled, and reach the screen only if it is on screen."""

    def __init__(self, app, conv: "Conversation | None"):
        self.app = app
        self.conv = conv

    def write(self, renderable) -> None:
        if self.conv is None:
            self.app.raw_log.write(renderable)
            return
        self.conv.append(renderable)
        if self.conv is self.app.active:
            self.app.raw_log.write(renderable)

    def clear(self) -> None:
        if self.conv is not None:
            self.conv.lines.clear()
        if self.conv is None or self.conv is self.app.active:
            self.app.raw_log.clear()


class ConvItem(Static):
    """One clickable conversation row in the sidebar."""

    def __init__(self, conv: Conversation):
        super().__init__(conv.sidebar_row(False), classes="conv")
        self.conv_key = conv.key

    def on_click(self) -> None:
        conv = self.app.convs.get(self.conv_key)
        if conv is not None:
            self.app.select_conv(conv)
