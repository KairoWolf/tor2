"""Checking for and applying updates from the git remote.

Deliberately conservative, because this executes code fetched from the
internet:

* only ever **fast-forwards** the current branch — never merges, rebases or
  resets, so local commits or edits stop an update rather than being lost;
* refuses to run if the working tree is dirty;
* refuses if the remote is not the expected repository.

On a server this is **off by default** and must be switched on by an admin.
"""

import subprocess
from pathlib import Path

REPO_URL_FRAGMENT = "KairoWolf/tor2"
GIT_TIMEOUT = 120


class UpdateError(Exception):
    pass


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _git(*args: str, cwd: Path | None = None) -> str:
    try:
        out = subprocess.run(("git", *args), cwd=str(cwd or repo_root()),
                             capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except FileNotFoundError:
        raise UpdateError("git is not installed") from None
    except subprocess.TimeoutExpired:
        raise UpdateError("git timed out") from None
    if out.returncode != 0:
        raise UpdateError((out.stderr or out.stdout).strip().splitlines()[-1]
                          if (out.stderr or out.stdout).strip() else "git failed")
    return out.stdout.strip()


def is_git_checkout() -> bool:
    try:
        return _git("rev-parse", "--is-inside-work-tree") == "true"
    except UpdateError:
        return False


def check() -> dict:
    """Fetch and report what an update would do. Does not modify the checkout.

    Returns {'behind': int, 'current': sha, 'latest': sha, 'summary': str}.
    """
    if not is_git_checkout():
        raise UpdateError("this copy of tor2 is not a git checkout — "
                          "update it the way you installed it")
    remote = _git("remote", "get-url", "origin")
    if REPO_URL_FRAGMENT not in remote:
        raise UpdateError(f"origin is {remote}, not the tor2 repository")
    if _git("status", "--porcelain"):
        raise UpdateError("you have local changes — commit or discard them first")

    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    _git("fetch", "--quiet", "origin", branch)
    current = _git("rev-parse", "HEAD")
    latest = _git("rev-parse", f"origin/{branch}")
    behind = int(_git("rev-list", "--count", f"HEAD..origin/{branch}") or 0)
    summary = ""
    if behind:
        summary = _git("log", "--oneline", "-5", f"HEAD..origin/{branch}")
    return {"behind": behind, "current": current[:8], "latest": latest[:8],
            "branch": branch, "summary": summary}


def apply() -> dict:
    """Fast-forward to the fetched remote head. Returns the check() result."""
    info = check()
    if not info["behind"]:
        return info
    _git("merge", "--ff-only", f"origin/{info['branch']}")
    return info
