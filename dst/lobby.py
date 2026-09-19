"""Klei 公共大厅客户端：列表、详情、Lua 字段解析。"""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from astrbot.api import logger

REGIONS = ("ap-east-1", "ap-southeast-1", "us-east-1", "eu-central-1")
LIST_URL = "https://lobby-v2-cdn.klei.com/{region}-{platform}.json.gz"
DETAIL_URL = "https://lobby-v2-{region}.klei.com/lobby/read"

_KEY_RE = re.compile(r"([{\s,])([A-Za-z_]\w*)\s*=")
_TRAIL_COMMA_RE = re.compile(r",(\s*[}\]])")


_PLACEHOLDER_NAMES = frozenset(
    {
        "",
        "host",
        "dedicated",
        "dedicated server",
        "server",
        "unknown",
        "服务器",
        "主机",
        "未知",
    }
)


@dataclass
class LobbyPlayer:
    name: str = ""
    prefab: str = ""
    netid: str = ""
    userid: str = ""
    eventlevel: int = 0

    @property
    def key(self) -> str:
        return self.netid or self.userid or self.name

    def display_id(self) -> str:
        if self.userid and self.userid.startswith("KU_"):
            return self.userid
        return "-"

    def is_placeholder(self) -> bool:
        """专用服务器空房时，大厅常塞一个没有昵称/角色/KU_ 的占位玩家。"""
        if (self.userid or "").startswith("KU_"):
            return False
        netid = (self.netid or "").strip()
        if netid.startswith("7656"):
            return False
        if netid and netid.lower() not in {"0", "unknown", "nil", "null"}:
            return False
        if (self.prefab or "").strip():
            return False
        return (self.name or "").strip().lower() in _PLACEHOLDER_NAMES


@dataclass
class LobbyServer:
    row_id: str = ""
    name: str = ""
    addr: str = ""
    port: int = 0
    connected: int = 0
    max_connections: int = 0
    season: str = ""
    mode: str = ""
    intent: str = ""
    dedicated: bool = False
    paused: bool = False
    has_password: bool = False
    mods: bool = False
    pvp: bool = False
    region: str = ""
    platform: str = ""
    day: int | None = None
    days_elapsed_in_season: int | None = None
    days_left_in_season: int | None = None
    players: list[LobbyPlayer] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def real_players(self) -> list[LobbyPlayer]:
        return [p for p in self.players if not p.is_placeholder()]

    @property
    def player_count(self) -> int:
        if self.players:
            return len(self.real_players)
        return self.connected


class LobbyError(Exception):
    """大厅请求或解析失败。"""


class LobbyClient:
    def __init__(self, proxy: str = "") -> None:
        self._proxy = proxy.strip() or None
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def fetch_list(self, region: str, platform: str) -> list[LobbyServer]:
        url = LIST_URL.format(region=region, platform=platform)
        session = await self._session_get()
        try:
            async with session.get(url, proxy=self._proxy) as resp:
                if resp.status != 200:
                    raise LobbyError(f"大厅列表 HTTP {resp.status}（{region}）")
                raw = await resp.read()
        except aiohttp.ClientError as exc:
            raise LobbyError(f"大厅列表请求失败：{exc}") from exc

        text = _decode_maybe_gzip(raw)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LobbyError(f"大厅列表不是合法 JSON：{exc}") from exc

        rows = payload.get("GET", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise LobbyError("大厅列表格式异常")

        servers: list[LobbyServer] = []
        for item in rows:
            if isinstance(item, dict):
                servers.append(_server_from_list_item(item, region, platform))
        return servers

    async def fetch_details(
        self, region: str, row_id: str, token: str
    ) -> dict[str, Any]:
        session = await self._session_get()
        url = DETAIL_URL.format(region=region)
        payloads = [
            {
                "__gameId": "DontStarveTogether",
                "__token": token,
                "query": {"__rowId": row_id},
            },
            {
                "gameId": "DontStarveTogether",
                "token": token,
                "query": {"rowId": row_id},
            },
        ]
        last_error = "未知错误"
        for payload in payloads:
            try:
                async with session.post(
                    url,
                    json=payload,
                    proxy=self._proxy,
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        last_error = f"大厅详情 HTTP {resp.status}"
                        continue
                    data = json.loads(text)
            except (aiohttp.ClientError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                continue

            if isinstance(data, dict) and data.get("error"):
                last_error = str(data.get("error"))
                if "TOKEN" in last_error.upper() or "AUTH" in last_error.upper():
                    raise LobbyError(
                        f"Token 无效或过期（{last_error}）。请检查 cluster_token.txt。"
                    )
                continue

            rows = data.get("GET") if isinstance(data, dict) else None
            if isinstance(rows, list) and rows:
                first = rows[0]
                if isinstance(first, dict):
                    return first
            last_error = "详情返回为空"
        raise LobbyError(f"无法读取房间详情：{last_error}")

    async def find_servers(
        self,
        *,
        region: str,
        platform: str,
        name: str,
        ip: str = "",
        port: int = 0,
    ) -> tuple[list[LobbyServer], str]:
        """返回 (匹配结果, 实际查询到的地区)。"""
        name = name.strip()
        ip = ip.strip()
        regions = list(REGIONS) if region == "auto" else [region]
        if region != "auto" and region not in REGIONS:
            regions = [region]

        last_error: Exception | None = None
        for current in regions:
            try:
                servers = await self.fetch_list(current, platform)
            except LobbyError as exc:
                last_error = exc
                logger.warning(f"查询大厅 {current} 失败：{exc}")
                continue
            matched = match_servers(servers, name=name, ip=ip, port=port)
            if matched:
                return matched, current

        if region != "auto":
            for current in REGIONS:
                if current == region:
                    continue
                try:
                    servers = await self.fetch_list(current, platform)
                except LobbyError as exc:
                    last_error = exc
                    continue
                matched = match_servers(servers, name=name, ip=ip, port=port)
                if matched:
                    return matched, current

        if last_error and not name and not ip:
            raise last_error
        return [], region if region != "auto" else ""


def match_servers(
    servers: list[LobbyServer],
    *,
    name: str,
    ip: str = "",
    port: int = 0,
) -> list[LobbyServer]:
    name_l = name.lower().strip()
    results: list[LobbyServer] = []
    for server in servers:
        if ip and server.addr != ip:
            continue
        if port and server.port != port:
            continue
        if name_l and name_l not in (server.name or "").lower():
            continue
        if not name_l and not ip and not port:
            continue
        results.append(server)
    exact = [s for s in results if (s.name or "").lower() == name_l]
    return exact or results


def _int_field(value: Any, fallback: int) -> int:
    if value is None or value == "":
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def apply_details(server: LobbyServer, details: dict[str, Any]) -> LobbyServer:
    server.raw.update(details)
    if "connected" in details:
        server.connected = _int_field(details.get("connected"), 0)
    if "maxconnections" in details:
        server.max_connections = _int_field(
            details.get("maxconnections"), server.max_connections
        )
    server.season = str(details.get("season") or server.season)
    server.mode = str(details.get("mode") or server.mode)
    if "serverpaused" in details:
        server.paused = bool(details.get("serverpaused"))
    players_raw = details.get("players")
    if players_raw is None:
        players_raw = details.get("player")
    parsed_players = [
        p for p in parse_players(players_raw) if not p.is_placeholder()
    ]
    if parsed_players:
        server.players = parsed_players
        server.connected = len(parsed_players)
    elif players_raw not in (None, ""):
        server.players = []
        server.connected = 0
    world = parse_world_data(details.get("data"))
    if world.get("day") is not None:
        server.day = int(world["day"])
    if world.get("dayselapsedinseason") is not None:
        server.days_elapsed_in_season = int(world["dayselapsedinseason"])
    if world.get("daysleftinseason") is not None:
        server.days_left_in_season = int(world["daysleftinseason"])
    if world.get("season"):
        server.season = str(world["season"])
    return server


def parse_players(raw: Any) -> list[LobbyPlayer]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [_player_from_dict(x) for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        if all(str(k).isdigit() for k in raw):
            return [_player_from_dict(v) for v in raw.values() if isinstance(v, dict)]
        return [_player_from_dict(raw)]
    if not isinstance(raw, str):
        return []
    text = raw.strip()
    if not text or text in {"return {}", "return { }", "{}", "[]"}:
        return []
    try:
        data = _lua_to_obj(text)
    except (json.JSONDecodeError, ValueError) as exc:
        fallback = _players_from_regex(text)
        if fallback:
            return fallback
        logger.warning(f"解析大厅玩家列表失败：{exc}")
        return []
    if isinstance(data, dict):
        values = list(data.values()) if _looks_like_array_table(data) else [data]
        return [_player_from_dict(v) for v in values if isinstance(v, dict)]
    if isinstance(data, list):
        return [_player_from_dict(v) for v in data if isinstance(v, dict)]
    return []


def parse_world_data(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        data = _lua_to_obj(raw.strip())
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(f"解析世界 data 失败：{exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _server_from_list_item(
    item: dict[str, Any], region: str, platform: str
) -> LobbyServer:
    port = item.get("port") or 0
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        port_i = 0
    return LobbyServer(
        row_id=str(item.get("__rowId") or item.get("rowId") or ""),
        name=str(item.get("name") or ""),
        addr=str(item.get("__addr") or item.get("addr") or ""),
        port=port_i,
        connected=int(item.get("connected") or 0),
        max_connections=int(item.get("maxconnections") or 0),
        season=str(item.get("season") or ""),
        mode=str(item.get("mode") or ""),
        intent=str(item.get("intent") or ""),
        dedicated=bool(item.get("dedicated")),
        paused=bool(item.get("serverpaused")),
        has_password=bool(item.get("password")),
        mods=bool(item.get("mods")),
        pvp=bool(item.get("pvp")),
        region=region,
        platform=platform,
        raw=item,
    )


def _extract_ku(data: dict[str, Any]) -> str:
    for key in ("userid", "userId", "user_id", "ku", "klei_id", "kleiId"):
        val = str(data.get(key) or "").strip()
        if val.startswith("KU_"):
            return val
    for value in data.values():
        if isinstance(value, str) and value.strip().startswith("KU_"):
            return value.strip()
    return ""


def _player_from_dict(data: dict[str, Any]) -> LobbyPlayer:
    userid = _extract_ku(data)
    netid = str(data.get("netid") or data.get("netId") or "")
    if userid.startswith("7656") and not netid:
        netid, userid = userid, ""
    if netid.startswith("KU_") and not userid:
        userid, netid = netid, ""
    return LobbyPlayer(
        name=str(data.get("name") or ""),
        prefab=str(data.get("prefab") or ""),
        netid=netid,
        userid=userid,
        eventlevel=int(data.get("eventlevel") or 0),
    )


def _looks_like_array_table(data: dict[str, Any]) -> bool:
    return bool(data) and all(str(k).isdigit() for k in data)


def _players_from_regex(text: str) -> list[LobbyPlayer]:
    blocks = re.split(r"\}\s*,\s*\{", text)
    players: list[LobbyPlayer] = []
    for block in blocks:
        name = _re_field(block, "name")
        netid = _re_field(block, "netid")
        prefab = _re_field(block, "prefab")
        userid = (
            _re_field(block, "userid")
            or _re_field(block, "userId")
            or _re_field(block, "user_id")
        )
        if not userid:
            ku_match = re.search(r"KU_[A-Za-z0-9]+", block)
            userid = ku_match.group(0) if ku_match else ""
        if name or netid or userid:
            players.append(
                _player_from_dict(
                    {
                        "name": name,
                        "netid": netid,
                        "prefab": prefab,
                        "userid": userid,
                    }
                )
            )
    return players


def _re_field(block: str, key: str) -> str:
    match = re.search(rf'{key}\s*=\s*"([^"]*)"', block)
    return match.group(1) if match else ""


def _decode_maybe_gzip(raw: bytes) -> str:
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw).decode("utf-8")
    try:
        return gzip.decompress(raw).decode("utf-8")
    except OSError:
        return raw.decode("utf-8", errors="replace")


def _lua_to_obj(text: str) -> Any:
    s = text.strip()
    if s.startswith("return"):
        s = s[6:].strip()
    s = _KEY_RE.sub(r'\1"\2":', s)
    s = s.replace("true", "true").replace("false", "false")
    s = re.sub(r"\bnil\b", "null", s)
    s = _TRAIL_COMMA_RE.sub(r"\1", s)
    return json.loads(s)
