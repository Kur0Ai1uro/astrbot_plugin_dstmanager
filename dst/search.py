"""物品 prefab 与控制台指令的本地模糊检索。"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

PLUGIN_DIR = Path(__file__).resolve().parent.parent
ITEMS_PATH = PLUGIN_DIR / "data" / "items.json"
COMMANDS_PATH = PLUGIN_DIR / "data" / "commands.json"
PAGE_SIZE = 5


def split_keyword_page(text: str) -> tuple[str, int]:
    raw = (text or "").strip()
    parts = raw.rsplit(None, 1)
    if len(parts) == 2 and parts[1].isdigit() and int(parts[1]) >= 1:
        return parts[0], int(parts[1])
    return raw, 1


def paginate(
    rows: list[dict[str, Any]], page: int, page_size: int = PAGE_SIZE
) -> tuple[list[dict[str, Any]], int, int, int]:
    total = len(rows)
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    current = min(max(1, page), pages)
    start = (current - 1) * page_size
    return rows[start : start + page_size], current, pages, total


def page_hint(command: str, keyword: str, page: int, pages: int, total: int) -> str:
    if total == 0 or pages <= 1:
        return ""
    nxt = page + 1 if page < pages else 1
    target = f"/{command} {nxt}" if not keyword else f"/{command} {keyword} {nxt}"
    return f"第 **{page}/{pages}** 页，共 {total} 条。下一页：`{target}`"


def _load_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


class LocalCatalog:
    def __init__(self) -> None:
        self.items = _load_list(ITEMS_PATH)
        self.commands = _load_list(COMMANDS_PATH)

    def search_items(self, keyword: str) -> list[dict[str, Any]]:
        return _search(self.items, keyword, fields=("prefab", "zh", "en", "aliases"))

    def search_commands(self, keyword: str) -> list[dict[str, Any]]:
        return _search(
            self.commands,
            keyword,
            fields=("name", "zh", "usage", "desc", "aliases"),
            fuzzy=True,
        )


_FOLD_RE = re.compile(r"[\s_\-:.'\"()\[\]/\\]+")


def _fold(text: str) -> str:
    return _FOLD_RE.sub("", (text or "").lower())


def _is_subseq(needle: str, haystack: str) -> bool:
    if not needle:
        return False
    i = 0
    for ch in haystack:
        if ch == needle[i]:
            i += 1
            if i == len(needle):
                return True
    return False


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _search(
    rows: list[dict[str, Any]],
    keyword: str,
    fields: tuple[str, ...],
    fuzzy: bool = False,
) -> list[dict[str, Any]]:
    q = keyword.strip().lower()
    if not q:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        score = _score_row(row, q, fields, fuzzy=fuzzy)
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in scored]


def _row_texts(row: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    texts: list[str] = []
    for field in fields:
        value = row.get(field)
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, list):
            texts.extend(str(x) for x in value)
    return texts


def _score_text(q: str, text: str, *, fuzzy: bool) -> int:
    low = (text or "").lower().strip()
    if not low:
        return 0
    if low == q:
        return 100
    if low.startswith(q):
        return 80
    if q in low:
        return 50 + max(0, 20 - low.find(q))

    qn, tn = _fold(q), _fold(low)
    if qn and tn:
        if qn == tn:
            return 76
        if tn.startswith(qn):
            return 64
        if qn in tn:
            return 46

    if not fuzzy or not qn:
        return 0
    if len(q) >= 2 and _is_subseq(q, low):
        return 36
    if len(qn) >= 2 and _is_subseq(qn, tn):
        return 34
    if 2 <= len(qn) <= 24 and tn:
        ratio = max(_ratio(qn, tn), _ratio(qn, tn[: max(len(qn) + 4, 8)]))
        if ratio >= 0.78:
            return int(18 + 28 * ratio)
        if ratio >= 0.66 and abs(len(qn) - len(tn)) <= 4:
            return int(10 + 16 * ratio)
    return 0


def _score_row(
    row: dict[str, Any],
    q: str,
    fields: tuple[str, ...],
    fuzzy: bool = False,
) -> int:
    texts = _row_texts(row, fields)
    fuzzy_texts = (
        _row_texts(row, ("name", "zh", "aliases", "usage")) if fuzzy else []
    )
    best = 0
    for text in texts:
        best = max(best, _score_text(q, text, fuzzy=False))
    if fuzzy:
        for text in fuzzy_texts:
            best = max(best, _score_text(q, text, fuzzy=True))
        tokens = [t for t in q.split() if t]
        if len(tokens) > 1:
            token_best = [
                max((_score_text(tok, text, fuzzy=True) for text in fuzzy_texts), default=0)
                for tok in tokens
            ]
            if token_best and all(score > 0 for score in token_best):
                best = max(best, min(token_best))
    return best
