# Contributing

## Running the tests

```sh
python3 -m venv .venv
.venv/bin/pip install -e . pytest pytest-timeout
.venv/bin/pytest tests -q
```

The suite takes a few minutes. It never touches the network: Tor is stubbed
out in `tests/conftest.py`, and your real `~/.config/tor2` is swapped for a
temporary directory, so running the tests cannot disturb your own contacts,
servers or settings.

`ffmpeg` is needed for the video and audio tests.

## What the tests cover

| file | area |
|---|---|
| `test_protocol.py` | double encryption, binary frames, forgery rejection |
| `test_serverdb.py` | invites, members, channels, history pruning, media |
| `test_server_flows.py` | auth, broadcast, permissions, upload/fetch, kick |
| `test_admin.py` | ban, unban, promote, demote and their guards |
| `test_multisession.py` | several live conversations, routing, switching |
| `test_session_stability.py` | the sidebar-rebuild disconnect regression, keepalive |
| `test_visibility.py` | output is visible with no conversation open |
| `test_unread_mentions.py` | unread badges, mention detection |
| `test_sidebar.py` | clicking and arrow-key channel switching |
| `test_ui_features.py` | progress readout, local echo, tab completion, settings |
| `test_parallel_transfer.py` | sharded transfer reassembly, ranged fetch |
| `test_encryption_at_rest.py` | nothing readable on disk; still serves correctly |
| `test_paging_backup_limits.py` | history paging, deletion, flood control, backup |
| `test_large_transfers.py` | 700 MB transfer stays within a flat memory budget |
| `test_progress_thumbnails.py` | ffmpeg progress parsing, video thumbnails |
| `test_video.py`, `test_bigvid_command.py` | video helpers and `/big-vid` |

## Conventions

- Every bug fix gets a test that fails before the fix and passes after it.
  Both the "random disconnect" and "nothing shows on startup" bugs reached
  users because no test covered that path.
- Keep the message surface small. The protocol carries chat and media and
  nothing that could run code on a peer.
- Changes to the wire format need a `MAGIC` bump in `tor2/proto.py`; old and
  new clients then refuse each other with a clear message instead of
  misbehaving.
