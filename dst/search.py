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


class CliError(ValueError):
    """指令参数写错。"""


_QUERY_FLAGS = ("--query", "--查", "-查", "-q")
_PAGE_FLAGS = ("--page", "--页", "-页", "-p")
_NAME_FLAGS = ("--name", "--名", "-名", "-n")
_KU_FLAGS = ("--ku", "--id", "-ku", "-id")


def _tokenize(text: str) -> list[tuple[str, bool]]:
    """返回 (文本, 是否由引号括起)。引号内的数字不会被当成页码。"""
    raw = (
        (text or "")
        .replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )
    tokens: list[tuple[str, bool]] = []
    index = 0
    length = len(raw)
    while index < length:
        while index < length and raw[index].isspace():
            index += 1
        if index >= length:
            break
        if raw[index] in {'"', "'"}:
            quote = raw[index]
            index += 1
            start = index
            while index < length and raw[index] != quote:
                index += 1
            if index >= length:
                raise CliError("引号没有成对。")
            tokens.append((raw[start:index], True))
            index += 1
            continue
        start = index
        while index < length and not raw[index].isspace():
            index += 1
        tokens.append((raw[start:index], False))
    return tokens


_PAGE_NUMBER = re.compile(r"[1-9][0-9]*")


def _flag_forms(token: str, flag: str) -> list[str]:
    if flag.isascii():
        return [token.lower()]
    return [token]


def _take_flag(token: str, flags: tuple[str, ...], *, page: bool = False):
    for flag in flags:
        for form in _flag_forms(token, flag):
            if form == flag:
                return flag, None
            if form.startswith(flag + "="):
                return flag, form[len(flag) + 1 :]
            if (
                page
                and form.startswith(flag)
                and _PAGE_NUMBER.fullmatch(form[len(flag) :])
            ):
                return flag, form[len(flag) :]
    return None, None


def _token_text(token: str | tuple[str, bool]) -> str:
    return token[0] if isinstance(token, tuple) else token


def _next_value(tokens: list, index: int, flag: str) -> str:
    if index + 1 >= len(tokens):
        raise CliError(f"`{flag}` 后面要跟值。")
    value = _token_text(tokens[index + 1])
    if value.startswith("-"):
        raise CliError(f"`{flag}` 后面要跟值。")
    return value


def _parse_page(value: str) -> int:
    if not _PAGE_NUMBER.fullmatch(value or ""):
        raise CliError(f"页码必须是大于 0 的半角整数，且不能有前导零：`{value}`。")
    return int(value)


def parse_player_admin(text: str) -> tuple[str, str, str] | None:
    """玩家指令的管理动作。不是时返回 None。否则为 (new|delete|delete-all, 昵称, KU_)。"""
    tokens = [token for token, _quoted in _tokenize(text)]
    if not tokens or tokens[0].lower() not in {"--new", "--delete"}:
        return None
    action = tokens[0].lower()
    rest = tokens[1:]
    if action == "--delete":
        if len(rest) == 1 and rest[0].lower() == "-a":
            return "delete-all", "", ""
        return "delete", " ".join(rest).strip(), ""
    if len(rest) < 2 or not re.fullmatch(r"KU_[A-Za-z0-9]+", rest[-1], re.I):
        return "new", "", ""
    return "new", " ".join(rest[:-1]).strip(), rest[-1]


def parse_search_args(text: str) -> tuple[str, int]:
    """返回 (查询内容, 页码)。引号外最后一个半角整数是页码，引号内原样保留。"""
    tokens = _tokenize(text)
    query: list[str] = []
    quoted: list[bool] = []
    page = 1
    seen_page = False
    seen_query = False
    index = 0
    while index < len(tokens):
        token, was_quoted = tokens[index]
        flag, inline = _take_flag(token, _QUERY_FLAGS)
        if flag:
            value = inline if inline is not None else _next_value(tokens, index, flag)
            if inline is None:
                index += 1
            if seen_query:
                raise CliError("`-查` 只能写一次。")
            seen_query = True
            query = [value]
            quoted = [True]
            index += 1
            continue
        flag, inline = _take_flag(token, _PAGE_FLAGS, page=True)
        if flag:
            value = inline if inline is not None else _next_value(tokens, index, flag)
            if inline is None:
                index += 1
            page = _parse_page(value)
            seen_page = True
            index += 1
            continue
        if token.startswith("-"):
            raise CliError(f"未知参数：`{token}`。翻页请用 `-p`。")
        query.append(token)
        quoted.append(was_quoted)
        index += 1
    if (
        not seen_page
        and not seen_query
        and query
        and not quoted[-1]
        and _PAGE_NUMBER.fullmatch(query[-1])
    ):
        page = int(query.pop())
    return " ".join(part for part in query if part).strip(), page


def parse_bind_args(text: str) -> tuple[str, str]:
    """返回 (昵称, KU_)。`-名` 与 `-ku`；否则昵称在前，KU_ 在最后。引号内空格会保留。"""
    tokens = [token for token, _quoted in _tokenize(text)]
    if not any(token.startswith("-") for token in tokens):
        if len(tokens) < 2:
            return "", ""
        if re.fullmatch(r"KU_[A-Za-z0-9]+", tokens[-1], re.I):
            return " ".join(tokens[:-1]).strip(), tokens[-1]
        if re.fullmatch(r"KU_[A-Za-z0-9]+", tokens[0], re.I):
            return " ".join(tokens[1:]).strip(), tokens[0]
        return "", ""
    name = ""
    ku = ""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        flag, inline = _take_flag(token, _NAME_FLAGS)
        if flag:
            value = inline if inline is not None else _next_value(tokens, index, flag)
            if inline is None:
                index += 1
            name = value.strip()
            index += 1
            continue
        flag, inline = _take_flag(token, _KU_FLAGS)
        if flag:
            value = inline if inline is not None else _next_value(tokens, index, flag)
            if inline is None:
                index += 1
            ku = value.strip()
            index += 1
            continue
        raise CliError(f"未知参数：`{token}`。可用 `-名`、`-ku`。")
    return name, ku


def paginate(
    rows: list[dict[str, Any]], page: int, page_size: int = PAGE_SIZE
) -> tuple[list[dict[str, Any]], int, int, int]:
    total = len(rows)
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    current = min(max(1, page), pages)
    start = (current - 1) * page_size
    return rows[start : start + page_size], current, pages, total


def _show_query(keyword: str) -> str:
    if not keyword:
        return ""
    if (
        _PAGE_NUMBER.fullmatch(keyword)
        or any(ch.isspace() for ch in keyword)
        or keyword.startswith("-")
    ):
        return f'"{keyword}"'
    return keyword


def page_overflow_text(command: str, query: str, page: int, pages: int) -> str:
    lines = [
        "**页码参数溢出**",
        "",
        f"没有第 {page} 页（共 {pages} 页）。指令可能写错。",
    ]
    if query:
        lines.append(
            f"若这个数字是查询内容的一部分，请写成 `/{command} \"{query} {page}\"`。"
        )
    else:
        lines.append(f"若要查询「{page}」本身，请写成 `/{command} \"{page}\"`。")
    return "\n".join(lines)


def page_hint(command: str, keyword: str, page: int, pages: int, total: int) -> str:
    if total == 0 or pages <= 1 or page >= pages:
        return ""
    nxt = page + 1
    shown = _show_query(keyword)
    target = f"/{command} -p {nxt}" if not shown else f"/{command} {shown} -p {nxt}"
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
