"""Small, trading-independent Polymarket changelog RSS monitor."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree


@dataclass(frozen=True)
class ChangelogEntry:
    entry_id: str
    title: str
    link: str
    published: str


def parse_changelog_feed(payload: bytes) -> tuple[ChangelogEntry, ...]:
    """Parse bounded RSS bytes without making any network request."""

    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise ValueError("Polymarket changelog RSS is invalid XML") from exc
    entries: list[ChangelogEntry] = []
    for item in root.findall("./channel/item"):
        title = str(item.findtext("title") or "").strip()
        link = str(item.findtext("link") or "").strip()
        published = str(item.findtext("pubDate") or "").strip()
        guid = str(item.findtext("guid") or "").strip()
        if not title or not link:
            continue
        entry_id = guid or hashlib.sha256(
            f"{title}\n{link}\n{published}".encode("utf-8")).hexdigest()
        entries.append(ChangelogEntry(entry_id, title, link, published))
    if not entries:
        raise ValueError("Polymarket changelog RSS contains no usable items")
    return tuple(entries)


def load_seen(path: Path) -> tuple[str, ...]:
    if not path.exists():
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("changelog monitor state is unreadable") from exc
    values = raw.get("seen_ids") if isinstance(raw, dict) else None
    if not isinstance(values, list) or not all(
            isinstance(value, str) and value for value in values):
        raise ValueError("changelog monitor state has invalid seen_ids")
    return tuple(values)


def save_seen(path: Path, entry_ids: tuple[str, ...]) -> None:
    """Atomically retain a bounded recent dedupe set."""

    path.parent.mkdir(parents=True, exist_ok=True)
    unique = tuple(dict.fromkeys(entry_ids))[:200]
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"seen_ids": unique}, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def check_feed(
    payload: bytes,
    state_path: Path,
    *,
    notify: Callable[[tuple[ChangelogEntry, ...]], bool] | None = None,
) -> tuple[ChangelogEntry, ...]:
    """Return new entries and commit them only after successful notification.

    The first healthy read establishes a baseline without replaying the entire
    historical feed. A failed notifier leaves state unchanged so the same new
    announcement is retried on the next isolated monitor run.
    """

    entries = parse_changelog_feed(payload)
    seen = load_seen(state_path)
    if not seen:
        save_seen(state_path, tuple(entry.entry_id for entry in entries))
        return ()
    known = set(seen)
    new_entries = tuple(entry for entry in entries if entry.entry_id not in known)
    if new_entries and notify is not None and not notify(new_entries):
        return new_entries
    save_seen(
        state_path,
        tuple(entry.entry_id for entry in entries) + seen)
    return new_entries
