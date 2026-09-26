"""大厅轮询：对比快照，产出进出/上下线文案。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

from .i18n import character_name, intent_name, mode_name, season_name
from .lobby import (
    LobbyClient,
    LobbyError,
    LobbyPlayer,
    LobbyServer,
    apply_details,
)
from .store import PluginStore, format_local_time


@dataclass
class Snapshot:
    found: bool = False
    missing_streak: int = 0
    server: LobbyServer | None = None
    players: dict[str, LobbyPlayer] = field(default_factory=dict)
    connected: int = 0


class ServerMonitor:
    def __init__(self, store: PluginStore) -> None:
        self.store = store
        self.snapshot = Snapshot()
        self._ready = False
        self.last_error = ""
        self.last_server: LobbyServer | None = None
        self.last_token_warning = ""
        self.last_ok_at = 0.0
        self.last_extra_matches = 0
        self._prefab_wait: dict[str, int] = {}

    async def poll(self, config: dict[str, Any], client: LobbyClient) -> list[str]:
        name = str(config.get("server_name") or "").strip()
        if not name and not str(config.get("server_ip") or "").strip():
            self.last_error = "未配置房间名或 IP，监测已暂停。"
            return []

        try:
            matched, region = await client.find_servers(
                region=str(config.get("region") or "ap-east-1"),
                platform=str(config.get("platform") or "Steam"),
                name=name,
                ip=str(config.get("server_ip") or "").strip(),
                port=int(config.get("server_port") or 0),
            )
        except LobbyError as exc:
            self.last_error = str(exc)
            logger.warning(f"大厅查询失败：{exc}")
            return []

        messages: list[str] = []
        if not matched:
            messages.extend(self._mark_missing(config))
            self.last_error = "大厅里没找到匹配的房间。请核对房间名、地区和平台。"
            return self._after_poll(messages)

        self.last_extra_matches = max(0, len(matched) - 1)
        if len(matched) > 1:
            logger.info(
                f"匹配到 {len(matched)} 个房间，使用「{matched[0].name}」。"
                "可填写 IP/端口以精确匹配。"
            )
        server = matched[0]
        server.region = region or server.region
        token = str(config.get("klei_token") or "").strip()
        self.last_token_warning = ""
        if token and server.row_id:
            try:
                details = await client.fetch_details(server.region, server.row_id, token)
                apply_details(server, details)
                self.store.enrich_players(server.players)
            except LobbyError as exc:
                self.last_token_warning = str(exc)
                logger.warning(f"读取房间详情失败：{exc}")
        elif not token:
            self.last_token_warning = "未配置 Token，无法显示玩家详情和谁进谁出。"

        joined, left, messages = self._mark_found(config, server)
        if self._ready:
            await self.store.record_players(joined, joined=True)
            await self.store.record_players(left, joined=False)
            if server.players:
                await self.store.record_players(server.players, joined=None)
        elif server.players:
            await self.store.record_players(server.players, joined=None)
        self.last_error = ""
        self.last_ok_at = time.monotonic()
        return self._after_poll(messages)

    def snapshot_fresh(self, interval: float) -> bool:
        if not self.last_server or not self.snapshot.found or not self.last_ok_at:
            return False
        return time.monotonic() - self.last_ok_at <= interval

    def _after_poll(self, messages: list[str]) -> list[str]:
        if not self._ready:
            self._ready = True
            return []
        return messages

    def _mark_missing(self, config: dict[str, Any]) -> list[str]:
        threshold = max(1, int(config.get("offline_threshold") or 3))
        was_found = self.snapshot.found
        self.snapshot.missing_streak += 1
        self.snapshot.found = False
        self.snapshot.players = {}
        self.snapshot.connected = 0
        if (
            was_found
            and self.snapshot.missing_streak >= threshold
            and bool(config.get("notify_online_offline", True))
        ):
            title = (self.last_server.name if self.last_server else "") or "目标房间"
            return [
                f"**[饥荒]** 「{md_escape(title)}」**已离开大厅**（可能关服、重启或未公开）。"
            ]
        return []

    def _mark_found(
        self, config: dict[str, Any], server: LobbyServer
    ) -> tuple[list[LobbyPlayer], list[LobbyPlayer], list[str]]:
        messages: list[str] = []
        prev = self.snapshot
        just_online = not prev.found
        incoming = {p.key: p for p in server.players if p.key}
        old_keys = set(prev.players)
        new_keys = set(incoming)
        joined = [incoming[k] for k in new_keys - old_keys]
        left = [prev.players[k] for k in old_keys - new_keys]

        if just_online and bool(config.get("notify_online_offline", True)):
            appear = [
                f"**[饥荒]** 「{md_escape(server.name)}」**已出现在大厅**"
                f"（{server.player_count}/{server.max_connections or '?'}）。"
            ]
            appear.extend(_online_digest(server))
            messages.append("\n".join(appear))

        skip_join_leave = just_online and bool(config.get("notify_online_offline", True))
        if bool(config.get("notify_join_leave", True)) and not skip_join_leave:
            notify_join = self._joins_to_notify(joined, incoming, prev)
            if notify_join or left:
                chunk: list[str] = []
                missing_ku: list[str] = []
                for player in notify_join:
                    chunk.append(_join_leave_line(server, player, joined=True))
                    if not (player.userid or "").startswith("KU_"):
                        missing_ku.append(player.name or "未知")
                for player in left:
                    chunk.append(_join_leave_line(server, player, joined=False))
                if missing_ku:
                    names = "、".join(md_escape(n) for n in missing_ku)
                    sample = missing_ku[0]
                    chunk.append(
                        f"{names} 尚未录入 KU_。录入：`/饥荒新玩家 \"{sample}\" KU_xxxxxxxx`"
                    )
                if chunk:
                    chunk.extend(_online_digest(server))
                    messages.append("\n".join(chunk))
            elif not incoming:
                old_count = prev.connected
                new_count = server.player_count
                if not just_online and old_count != new_count:
                    hint = ""
                    if self.last_token_warning:
                        hint = "；未配置有效 Token，只能看到人数"
                    messages.append(
                        f"**[饥荒]** 在线人数 **{old_count} → {new_count}**"
                        f"（{new_count}/{server.max_connections or '?'}{hint}）"
                    )

        self.last_server = server
        self.snapshot = Snapshot(
            found=True,
            missing_streak=0,
            server=server,
            players=incoming,
            connected=server.player_count,
        )
        return joined, left, messages

    def _joins_to_notify(
        self,
        joined: list[LobbyPlayer],
        incoming: dict[str, LobbyPlayer],
        prev: Snapshot,
    ) -> list[LobbyPlayer]:
        """大厅刚看到人时 prefab 经常是空的，先等一轮再推送。"""
        notify: list[LobbyPlayer] = []
        seen: set[str] = set()
        for player in joined:
            if self._announce_join(player):
                notify.append(player)
            seen.add(player.key)
        for key in list(self._prefab_wait):
            player = incoming.get(key)
            if player is None:
                self._prefab_wait.pop(key, None)
                held = prev.players.get(key)
                if held is not None:
                    notify.append(held)
                continue
            if key in seen:
                continue
            if self._announce_join(player):
                notify.append(player)
        return notify

    def _announce_join(self, player: LobbyPlayer) -> bool:
        if (player.prefab or "").strip():
            self._prefab_wait.pop(player.key, None)
            return True
        waits = self._prefab_wait.get(player.key, 0)
        if waits >= 1:
            self._prefab_wait.pop(player.key, None)
            return True
        self._prefab_wait[player.key] = waits + 1
        return False


def md_escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("*", "\\*")
    )


def _md_cell(text: str) -> str:
    return md_escape(str(text).replace("|", "｜").replace("\n", " "))


def _md_table(
    headers: list[str],
    rows: list[list[str]],
    aligns: list[str] | None = None,
) -> str:
    seps = []
    for i, _header in enumerate(headers):
        seps.append(aligns[i] if aligns and i < len(aligns) else "---")
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(seps) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def format_player_table(players: list[LobbyPlayer]) -> str:
    if not players:
        return ""
    lines = [
        "| 序号 | 昵称 | 角色 | KU_ |",
        "| ---: | --- | --- | --- |",
    ]
    for i, player in enumerate(players, 1):
        ku = player.display_id()
        ku_cell = f"`{_md_cell(ku)}`" if ku != "-" else "-"
        lines.append(
            f"| {i} | {_md_cell(player.name or '未知')} | "
            f"{_md_cell(character_name(player.prefab))} | {ku_cell} |"
        )
    return "\n".join(lines)


def _online_digest(server: LobbyServer) -> list[str]:
    if not server.real_players:
        return ["当前在线：无人"]
    lines = [f"当前在线 {len(server.real_players)}/{server.max_connections or '?'}"]
    table = format_player_table(server.real_players)
    if table:
        lines.append("")
        lines.append(table)
    return lines


def _join_leave_line(server: LobbyServer, player: LobbyPlayer, joined: bool) -> str:
    action = "进入服务器" if joined else "离开服务器"
    char = character_name(player.prefab)
    pid = player.display_id()
    name = md_escape(player.name or "未知玩家")
    return (
        f"**[饥荒]** {name} **{action}**"
        f"（{md_escape(char)} / `{md_escape(pid)}`，"
        f"{server.player_count}/{server.max_connections or '?'}）"
    )


def format_status(
    server: LobbyServer | None,
    *,
    error: str = "",
    token_warning: str = "",
    extra_matches: int = 0,
) -> str:
    if error and not server:
        return f"**查询失败**\n\n{md_escape(error)}"
    if not server:
        return f"**饥荒状态**\n\n{md_escape(error or '还没有查到服务器。')}"

    progress = _day_suffix(server).strip("（）") or "-"
    lines = [
        f"**{md_escape(server.name)}** · {server.player_count}/{server.max_connections or '?'}",
        "",
        _md_table(
            ["模式", "倾向", "季节", "进度"],
            [
                [
                    _md_cell(mode_name(server.mode)),
                    _md_cell(intent_name(server.intent)),
                    _md_cell(season_name(server.season)),
                    _md_cell(progress),
                ]
            ],
        ),
    ]
    flags: list[str] = []
    if server.paused:
        flags.append("已暂停")
    if server.has_password:
        flags.append("有密码")
    if server.mods:
        flags.append("有模组")
    if server.pvp:
        flags.append("PvP")
    if flags:
        lines.append(" · ".join(flags))
    if extra_matches > 0:
        lines.append(f"另匹配到 {extra_matches} 个同名房间，已显示第一个")
    if token_warning:
        lines.append(md_escape(token_warning))
    if server.real_players:
        table = format_player_table(server.real_players)
        if table:
            lines.append("")
            lines.append(table)
    elif token_warning:
        lines.append("暂无玩家详情")
    else:
        lines.append("当前无人在线")
    if error:
        lines.append(md_escape(error))
    return "\n".join(lines)


def format_player_rows(
    rows: list[dict[str, Any]], keyword: str, start: int = 1, *, listing: bool = False
) -> str:
    title = "**玩家列表**" if listing else f"**玩家检索** · `{md_escape(keyword)}`"
    if not rows:
        empty = "进过服后才会留下记录。" if listing else "没有找到，进过服后才会留下记录。"
        return f"{title}\n{empty}"
    table_rows: list[list[str]] = []
    for i, row in enumerate(rows, start):
        names = row.get("names") or [row.get("name")]
        name_text = " / ".join(str(x) for x in names if x)
        ku = str(row.get("userid") or "")
        ku_cell = f"`{_md_cell(ku)}`" if ku.startswith("KU_") else "-"
        table_rows.append(
            [
                str(i),
                _md_cell(name_text or "未知"),
                _md_cell(character_name(str(row.get("prefab") or ""))),
                ku_cell,
                str(row.get("join_count") or 0),
                _md_cell(format_local_time(str(row.get("last_seen") or ""))),
            ]
        )
    return (
        f"{title}\n\n"
        + _md_table(
            ["序号", "昵称", "角色", "KU_", "进服", "上次见到"],
            table_rows,
            ["---:", "---", "---", "---", "---:", "---"],
        )
    )


def format_items(
    rows: list[dict[str, Any]], keyword: str, start: int = 1
) -> str:
    title = f"**物品检索** · `{md_escape(keyword)}`"
    if not rows:
        return f"{title}\n没有找到，可试中文名、英文名或 prefab。"
    table_rows: list[list[str]] = []
    for i, row in enumerate(rows, start):
        prefab = str(row.get("prefab") or "?")
        give = str(row.get("give") or f'c_give("{prefab}", 1)')
        table_rows.append(
            [
                str(i),
                _md_cell(str(row.get("zh") or "")),
                _md_cell(str(row.get("en") or "")),
                f"`{_md_cell(prefab)}`",
                f"`{_md_cell(give)}`",
            ]
        )
    return (
        f"{title}\n\n"
        + _md_table(
            ["序号", "中文", "英文", "prefab", "给予"],
            table_rows,
            ["---:", "---", "---", "---", "---"],
        )
    )


def format_commands(
    rows: list[dict[str, Any]], keyword: str, start: int = 1
) -> str:
    title = f"**指令检索** · `{md_escape(keyword)}`"
    if not rows:
        return f"{title}\n没有找到，可试中文或命令名，如 `give`、`godmode`。"
    table_rows: list[list[str]] = []
    for i, row in enumerate(rows, start):
        usage = str(row.get("usage") or row.get("name") or "")
        table_rows.append(
            [
                str(i),
                _md_cell(str(row.get("zh") or row.get("name") or "")),
                f"`{_md_cell(usage)}`",
                "需管理员" if row.get("admin") else "-",
                _md_cell(str(row.get("desc") or "-")),
            ]
        )
    return (
        f"{title}\n\n"
        + _md_table(
            ["序号", "名称", "用法", "权限", "说明"],
            table_rows,
            ["---:", "---", "---", "---", "---"],
        )
        + "\n\n只检索用法，不会代为执行。"
    )


def _day_suffix(server: LobbyServer) -> str:
    parts: list[str] = []
    if server.day is not None:
        parts.append(f"第 {server.day} 天")
    if server.days_left_in_season is not None:
        parts.append(f"本季剩余 {server.days_left_in_season} 天")
    if not parts:
        return ""
    return "（" + "，".join(parts) + "）"
