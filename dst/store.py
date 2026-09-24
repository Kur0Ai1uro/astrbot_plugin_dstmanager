"""本服玩家历史与订阅会话，写到 AstrBot data 目录。"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .lobby import LobbyPlayer

PLUGIN_NAME = "astrbot_plugin_dst"
PLUGIN_DIR = Path(__file__).resolve().parent.parent
TZ_SHANGHAI = timezone(timedelta(hours=8))
_KU_LINE = re.compile(r"^(KU_[A-Za-z0-9]+)\s*=\s*(.+?)\s*$")


def _now_iso() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")


def _shown_in_history(row: dict[str, Any]) -> bool:
    """只展示真正进过服的人。KU_test / 未进服的测试录入不算。"""
    userid = str(row.get("userid") or "").strip()
    if userid.upper() == "KU_TEST":
        return False
    names = [str(row.get("name") or ""), *(row.get("names") or [])]
    dummy_name = any(str(name).strip().lower() == "test" for name in names)
    if dummy_name and not str(row.get("prefab") or "").strip():
        return False
    return int(row.get("join_count") or 0) > 0


def format_local_time(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "-"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")


def _data_dir() -> Path:
    try:
        from astrbot.api.star import StarTools

        return Path(StarTools.get_data_dir(PLUGIN_NAME))
    except Exception:
        fallback = Path(__file__).resolve().parent.parent / "runtime_data"
        fallback.mkdir(parents=True, exist_ok=True)
        logger.warning(f"无法使用 StarTools 数据目录，回退到 {fallback}")
        return fallback


class PluginStore:
    def __init__(self) -> None:
        self._dir = _data_dir()
        self._players_path = self._dir / "players.json"
        self._subs_path = self._dir / "subscriptions.json"
        self._lock = asyncio.Lock()
        self.players: dict[str, dict[str, Any]] = self._load_json(
            self._players_path, {}
        )
        raw_subs = self._load_json(self._subs_path, {"sessions": []})
        sessions = raw_subs.get("sessions", []) if isinstance(raw_subs, dict) else []
        self.subscriptions: list[str] = [
            str(x) for x in sessions if isinstance(x, str) and x
        ]
        self.ku_by_name: dict[str, str] = {}
        self._load_ku_maps()
        self._backfill_ku()

    def _load_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"读取 {path} 失败，将使用默认值：{exc}")
            return default

    async def _write_json(self, path: Path, data: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        text = json.dumps(data, ensure_ascii=False, indent=2)
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    def _load_ku_maps(self) -> None:
        paths = [
            PLUGIN_DIR / "data" / "player.txt",
            self._dir / "player.txt",
        ]
        for path in paths:
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8-sig")
            except OSError as exc:
                logger.warning(f"读取 {path} 失败：{exc}")
                continue
            count = 0
            for raw in text.splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                match = _KU_LINE.match(line)
                if not match:
                    continue
                ku, name = match.group(1), match.group(2).strip()
                if name:
                    self.ku_by_name[name] = ku
                    count += 1
            if count:
                logger.info(f"已从 {path.name} 载入 {count} 条 KU_ 对照")

    def _write_player_txt(self) -> None:
        path = self._dir / "player.txt"
        ordered: list[str] = []
        if path.is_file():
            try:
                for raw in path.read_text(encoding="utf-8-sig").splitlines():
                    match = _KU_LINE.match(raw.strip())
                    if match:
                        ordered.append(match.group(2).strip())
            except OSError as exc:
                logger.warning(f"读取 {path} 失败：{exc}")
        names = list(dict.fromkeys([*ordered, *self.ku_by_name.keys()]))
        lines = [
            f"{self.ku_by_name[name]}={name}"
            for name in names
            if name and self.ku_by_name.get(name)
        ]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    async def add_ku_mapping(self, name: str, ku: str) -> dict[str, str]:
        name = (name or "").strip()
        ku = (ku or "").strip()
        if ku.upper().startswith("KU_"):
            ku = "KU_" + ku[3:]
        old = self.lookup_ku(name)
        async with self._lock:
            self.ku_by_name[name] = ku
            self._write_player_txt()
            now = _now_iso()
            row = None
            for existing in self.players.values():
                names = [
                    str(existing.get("name") or ""),
                    *(existing.get("names") or []),
                ]
                if name in names or str(existing.get("userid") or "") == ku:
                    row = existing
                    break
            if not row:
                row = {
                    "key": ku,
                    "name": name,
                    "names": [name],
                    "userid": ku,
                    "netid": "",
                    "prefab": "",
                    "first_seen": now,
                    "last_seen": now,
                    "join_count": 0,
                    "last_action": "",
                }
                self.players[ku] = row
            else:
                old_key = str(row.get("key") or "")
                names = list(row.get("names") or [])
                if name not in names:
                    names.append(name)
                row["name"] = row.get("name") or name
                row["names"] = names
                row["userid"] = ku
                row["key"] = ku
                row["last_seen"] = now
                self.players[ku] = row
                if old_key and old_key != ku:
                    self.players.pop(old_key, None)
            await self._write_json(self._players_path, self.players)
        if old == ku:
            status = "unchanged"
        elif old:
            status = "updated"
        else:
            status = "added"
        return {"status": status, "name": name, "ku": ku, "old_ku": old}

    def lookup_ku(self, name: str) -> str:
        key = (name or "").strip()
        if not key:
            return ""
        if key in self.ku_by_name:
            return self.ku_by_name[key]
        lowered = key.lower()
        for mapped_name, ku in self.ku_by_name.items():
            if mapped_name.lower() == lowered:
                return ku
        return ""

    def enrich_players(self, players: list[LobbyPlayer]) -> list[LobbyPlayer]:
        for player in players:
            if player.userid and player.userid.startswith("KU_"):
                if player.name:
                    self.ku_by_name[player.name] = player.userid
                continue
            ku = self.lookup_ku(player.name)
            if ku:
                player.userid = ku
        return players

    def _backfill_ku(self) -> None:
        changed = False
        for row in self.players.values():
            userid = str(row.get("userid") or "")
            if userid.startswith("KU_"):
                name = str(row.get("name") or "")
                if name:
                    self.ku_by_name[name] = userid
                continue
            ku = ""
            for name in [row.get("name"), *(row.get("names") or [])]:
                ku = self.lookup_ku(str(name or ""))
                if ku:
                    break
            if ku:
                row["userid"] = ku
                changed = True
        if changed:
            try:
                self._players_path.write_text(
                    json.dumps(self.players, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                logger.warning(f"回写玩家 KU_ 失败：{exc}")

    def player_key(self, player: LobbyPlayer) -> str:
        return player.userid or player.netid or player.name

    async def record_players(
        self, players: list[LobbyPlayer], joined: bool | None
    ) -> None:
        if not players:
            return
        async with self._lock:
            now = _now_iso()
            changed = False
            for player in players:
                if not player.userid:
                    player.userid = self.lookup_ku(player.name)
                key = self.player_key(player)
                if not key:
                    continue
                row = self.players.get(key) or self._find_row(player)
                if not row:
                    row = {}
                names = list(row.get("names") or [])
                if player.name and player.name not in names:
                    names.append(player.name)
                if not row:
                    row = {
                        "key": key,
                        "first_seen": now,
                        "join_count": 0 if joined is False else 1,
                        "last_action": "leave" if joined is False else "join",
                    }
                elif joined is True:
                    row["last_action"] = "join"
                    row["join_count"] = int(row.get("join_count") or 0) + 1
                elif joined is False:
                    row["last_action"] = "leave"
                row["name"] = player.name or row.get("name") or ""
                row["names"] = names
                row["userid"] = (
                    player.userid
                    or row.get("userid")
                    or self.lookup_ku(player.name)
                    or ""
                )
                row["netid"] = player.netid or row.get("netid") or ""
                if player.name and row["userid"].startswith("KU_"):
                    self.ku_by_name[player.name] = row["userid"]
                row["prefab"] = player.prefab or row.get("prefab") or ""
                row["last_seen"] = now
                old_key = str(row.get("key") or "")
                row["key"] = key
                self.players[key] = row
                if old_key and old_key != key:
                    self.players.pop(old_key, None)
                changed = True
            if changed:
                await self._write_json(self._players_path, self.players)

    def _find_row(self, player: LobbyPlayer) -> dict[str, Any] | None:
        if player.userid:
            for row in self.players.values():
                if str(row.get("userid") or "") == player.userid:
                    return row
        if player.netid:
            for row in self.players.values():
                if str(row.get("netid") or "") == player.netid:
                    return row
        name = (player.name or "").strip()
        if name:
            for row in self.players.values():
                names = [str(row.get("name") or ""), *(row.get("names") or [])]
                if name in names:
                    return row
        return None

    def list_players(self) -> list[dict[str, Any]]:
        rows = [row for row in self.players.values() if _shown_in_history(row)]
        rows.sort(key=lambda row: str(row.get("last_seen") or ""), reverse=True)
        return rows

    def search_players(self, keyword: str) -> list[dict[str, Any]]:
        q = keyword.strip().lower()
        if not q:
            return []
        scored: list[tuple[int, dict[str, Any]]] = []
        for row in self.players.values():
            if not _shown_in_history(row):
                continue
            blob = " ".join(
                [
                    str(row.get("name") or ""),
                    " ".join(row.get("names") or []),
                    str(row.get("userid") or ""),
                    str(row.get("netid") or ""),
                    str(row.get("key") or ""),
                ]
            ).lower()
            if q not in blob:
                continue
            score = 0
            if str(row.get("userid") or "").lower() == q:
                score += 100
            if str(row.get("netid") or "").lower() == q:
                score += 90
            if str(row.get("name") or "").lower() == q:
                score += 80
            if str(row.get("name") or "").lower().startswith(q):
                score += 20
            score += min(int(row.get("join_count") or 0), 20)
            scored.append((score, row))
        scored.sort(key=lambda x: (-x[0], str(x[1].get("last_seen") or "")), reverse=False)
        scored.sort(key=lambda x: x[0], reverse=True)
        return [row for _, row in scored]

    def list_subscriptions(self) -> list[str]:
        return list(self.subscriptions)

    async def add_subscription(self, umo: str) -> bool:
        async with self._lock:
            if umo in self.subscriptions:
                return False
            self.subscriptions.append(umo)
            await self._write_json(
                self._subs_path, {"sessions": self.subscriptions}
            )
            return True

    async def remove_subscription(self, umo: str) -> bool:
        async with self._lock:
            if umo not in self.subscriptions:
                return False
            self.subscriptions = [x for x in self.subscriptions if x != umo]
            await self._write_json(
                self._subs_path, {"sessions": self.subscriptions}
            )
            return True
