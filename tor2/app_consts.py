"""Small values shared by the direct-message and server-mode UIs.

Kept in their own module so clientserver.py can use them without importing
app.py, which imports clientserver.py.
"""

from pathlib import Path

RECEIVED_DIR = Path.cwd() / "received"


def fmt_size(n: int) -> str:
    if n < 1024 * 1024:
        return f"{max(1, n // 1024)} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"
