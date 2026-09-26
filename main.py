import asyncio
import re
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

try:
    from dst.lobby import LobbyClient, LobbyError, apply_details
    from dst.monitor import (
        ServerMonitor,
        format_commands,
        format_items,
        format_player_rows,
        format_status,
    )
    from dst.search import (
        PAGE_SIZE,
        CliError,
        LocalCatalog,
        page_hint,
        page_overflow_text,
        paginate,
        parse_bind_args,
        parse_player_admin,
        parse_search_args,
    )
    from dst.store import PluginStore
except ImportError:  # AstrBot 把插件当包加载时
    from .dst.lobby import LobbyClient, LobbyError, apply_details
    from .dst.monitor import (
        ServerMonitor,
        format_commands,
        format_items,
        format_player_rows,
        format_status,
    )
    from .dst.search import (
        PAGE_SIZE,
        CliError,
        LocalCatalog,
        page_hint,
        page_overflow_text,
        paginate,
        parse_bind_args,
        parse_player_admin,
        parse_search_args,
    )
    from .dst.store import PluginStore

_PREFIXES = ("/饥荒", "饥荒", "/dst", "dst")
_SUBCOMMANDS = (
    "取消订阅",
    "unsubscribe",
    "指令列表",
    "帮助",
    "help",
    "菜单",
    "订阅",
    "subscribe",
    "状态",
    "status",
    "在线",
    "online",
    "新玩家",
    "newplayer",
    "玩家",
    "player",
    "物品",
    "item",
    "指令",
    "command",
    "cmd",
)

HELP_MARKDOWN = """**饥荒助手** · `/指令 参数`

| 指令 | 说明 |
| --- | --- |
| `/饥荒帮助` | 本说明 |
| `/饥荒状态` | 房间状态与当前在线 |
| `/饥荒玩家` | 全部历史。翻页：`/饥荒玩家 -p 2` |
| `/饥荒玩家 张三` | 检索。名字含空格或数字时加引号：`/饥荒玩家 "张三 2"` |
| `/饥荒玩家 --new "名字" KU_` | 补全对照 |
| `/饥荒玩家 --delete 名字` | 删除一条对照。清空：`--delete -A` |
| `/饥荒物品 金块` | 物品 prefab。翻页：`/饥荒物品 金块 -p 2` |
| `/饥荒指令 封禁` | 控制台用法（不执行）。翻页：`/饥荒指令 封禁 -p 2` |

**管理**（仅管理员）

| 指令 | 说明 |
| --- | --- |
| `/饥荒订阅` | 推送进出 |
| `/饥荒取消订阅` | 取消推送 |

英文别名把「饥荒」换成 `dst`，如 `/dst状态`。
"""


@register(
    "astrbot_plugin_dst",
    "yourname",
    "饥荒联机版助手：大厅监测、玩家进出推送、物品/玩家/指令检索",
    "1.2.1",
)
class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.store = PluginStore()
        self.catalog = LocalCatalog()
        self.monitor = ServerMonitor(self.store)
        self._client: LobbyClient | None = None
        self._client_proxy = ""
        self._task: asyncio.Task | None = None

    async def initialize(self):
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("饥荒助手已启动监测任务")

    async def terminate(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client:
            await self._client.close()
            self._client = None

    async def _ensure_client(self) -> LobbyClient:
        proxy = str(self.config.get("http_proxy") or "").strip()
        if self._client is None or self._client_proxy != proxy:
            if self._client is not None:
                await self._client.close()
            self._client = LobbyClient(proxy)
            self._client_proxy = proxy
        return self._client

    async def _monitor_loop(self):
        while True:
            interval = 45
            try:
                interval = max(15, int(self.config.get("poll_interval") or 45))
                messages = await self.monitor.poll(
                    self.config, await self._ensure_client()
                )
                for text in messages:
                    await self._broadcast(text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(f"饥荒监测循环异常：{exc}")
            await asyncio.sleep(interval)

    async def _broadcast(self, text: str):
        sessions = self.store.list_subscriptions()
        if not sessions:
            return
        chain = MessageChain().message(text)
        if hasattr(chain, "use_markdown"):
            chain.use_markdown(True)
        for umo in sessions:
            try:
                await self.context.send_message(umo, chain)
            except Exception as exc:
                logger.warning(f"向订阅会话推送失败：{exc}")

    async def _fresh_server(self):
        name = str(self.config.get("server_name") or "").strip()
        ip = str(self.config.get("server_ip") or "").strip()
        if not name and not ip:
            return None, 0, "请先在 AstrBot 插件配置里填写房间名（或 IP）。"
        interval = max(15, int(self.config.get("poll_interval") or 45))
        if self.monitor.snapshot_fresh(interval):
            return self.monitor.last_server, self.monitor.last_extra_matches, ""
        client = await self._ensure_client()
        try:
            matched, region = await client.find_servers(
                region=str(self.config.get("region") or "ap-east-1"),
                platform=str(self.config.get("platform") or "Steam"),
                name=name,
                ip=ip,
                port=int(self.config.get("server_port") or 0),
            )
        except LobbyError as exc:
            return None, 0, str(exc)
        if not matched:
            return None, 0, "大厅里没找到匹配的房间。请核对房间名、地区（可改成 auto）和平台。"
        server = matched[0]
        server.region = region or server.region
        token = str(self.config.get("klei_token") or "").strip()
        self.monitor.last_token_warning = ""
        if token and server.row_id:
            try:
                details = await client.fetch_details(server.region, server.row_id, token)
                apply_details(server, details)
                self.store.enrich_players(server.players)
            except LobbyError as exc:
                self.monitor.last_token_warning = str(exc)
        elif not token:
            self.monitor.last_token_warning = "未配置 Token，无法显示玩家详情。见 README「如何获取 Token」。"
        self.monitor.last_server = server
        self.monitor.last_extra_matches = max(0, len(matched) - 1)
        self.monitor.last_ok_at = time.monotonic()
        if server.players:
            await self.store.record_players(server.players, joined=None)
        return server, max(0, len(matched) - 1), ""

    def _rest_keyword(self, event: AstrMessageEvent, first: str = "") -> str:
        text = (event.message_str or "").strip()
        text = re.sub(r"^[/／]", "", text)
        lowered = text.lower()
        for prefix in _PREFIXES:
            if lowered.startswith(prefix.lower()):
                text = text[len(prefix) :].strip()
                lowered = text.lower()
                break
        for sub in _SUBCOMMANDS:
            if lowered.startswith(sub.lower()):
                text = text[len(sub) :].strip()
                break
        return text or (first or "").strip()

    def _parse_name_ku(self, text: str) -> tuple[str, str]:
        parts = (text or "").split()
        if len(parts) < 2:
            return "", ""
        if re.fullmatch(r"KU_[A-Za-z0-9]+", parts[-1], re.I):
            return " ".join(parts[:-1]).strip(), parts[-1]
        if re.fullmatch(r"KU_[A-Za-z0-9]+", parts[0], re.I):
            return " ".join(parts[1:]).strip(), parts[0]
        return " ".join(parts[:-1]).strip(), parts[-1]

    @filter.command_group("饥荒", alias={"dst"})
    def dst(self):
        """饥荒联机版助手：状态、在线、玩家、物品、指令、订阅"""

    @filter.command("饥荒状态", alias={"dst状态", "饥荒在线", "dst在线"})
    async def cmd_status_alias(self, event: AstrMessageEvent):
        """查看饥荒服务器当前状态"""
        async for result in self.cmd_status(event):
            yield result

    @dst.command("状态", alias={"status", "在线", "online"})
    async def cmd_status(self, event: AstrMessageEvent):
        """查看房间名、人数、季节和在线玩家"""
        server, extra, error = await self._fresh_server()
        yield self._md(
            event,
            format_status(
                server,
                error=error,
                token_warning=self.monitor.last_token_warning,
                extra_matches=extra,
            ),
        )

    async def _player_admin(self, event: AstrMessageEvent, admin: tuple[str, str, str]):
        action, name, ku = admin
        if action == "delete-all":
            count = await self.store.delete_all_ku_mappings()
            yield self._md(event, f"**已清空对照**\n\n删除了 {count} 条 `KU_` 对照。进服历史仍保留。")
            return
        if action == "delete":
            if not name:
                yield self._md(
                    event,
                    "**用法**\n\n`/饥荒玩家 --delete 昵称`\n`/饥荒玩家 --delete KU_xxxxxxxx`\n`/饥荒玩家 --delete -A`",
                )
                return
            removed = await self.store.delete_ku_mapping(name)
            if not removed:
                yield self._md(event, f"**没有这条对照**\n\n`{name}`")
                return
            lines = ["**已删除对照**", "", "| 昵称 | KU_ |", "| --- | --- |"]
            for mapped_name, mapped_ku in removed:
                lines.append(f"| {mapped_name.replace('|', '｜')} | `{mapped_ku}` |")
            yield self._md(event, "\n".join(lines))
            return
        if not name or not ku:
            yield self._md(
                event,
                "**用法**\n\n`/饥荒玩家 --new \"昵称\" KU_xxxxxxxx`",
            )
            return
        if not re.fullmatch(r"KU_[A-Za-z0-9]+", ku, re.I):
            yield self._md(event, f"KU_ID 格式不对：`{ku}`。应类似 `KU_xxxxxxxx`。")
            return
        result = await self.store.add_ku_mapping(name, ku)
        if self.monitor.last_server and self.monitor.last_server.players:
            self.store.enrich_players(self.monitor.last_server.players)
        title = {"unchanged": "**已有对照**", "updated": "**已更新**"}.get(
            result["status"], "**已录入**"
        )
        yield self._md(
            event,
            f"{title}\n\n| 昵称 | KU_ |\n| --- | --- |\n| {result['name'].replace('|', '｜')} | `{result['ku']}` |",
        )

    @filter.command("饥荒玩家", alias={"dst玩家"})
    async def cmd_player_alias(self, event: AstrMessageEvent, keyword: str = ""):
        """检索本服历史玩家 ID"""
        async for result in self.cmd_player(event, keyword):
            yield result

    @dst.command("玩家", alias={"player"})
    async def cmd_player(self, event: AstrMessageEvent, keyword: str = ""):
        """按昵称 / KU_ID 查本服历史玩家；不带关键词则列出全部"""
        kw = self._rest_keyword(event, keyword)
        try:
            admin = parse_player_admin(kw)
        except CliError as exc:
            yield self._md(event, f"**参数错误**\n\n{exc}")
            return
        if admin is not None:
            async for result in self._player_admin(event, admin):
                yield result
            return
        try:
            query, page = parse_search_args(kw)
        except CliError as exc:
            yield self._md(event, f"**参数错误**\n\n{exc}")
            return
        source = self.store.list_players() if not query else self.store.search_players(query)
        pages = max(1, (len(source) + PAGE_SIZE - 1) // PAGE_SIZE) if source else 1
        if page > pages:
            yield self._md(event, page_overflow_text("饥荒玩家", query, page, pages))
            return
        page_rows, page, pages, total = paginate(source, page)
        start = (page - 1) * PAGE_SIZE + 1
        text = format_player_rows(page_rows, query, start=start, listing=not query)
        hint = page_hint("饥荒玩家", query, page, pages, total)
        yield self._md(event, f"{text}\n\n{hint}" if hint else text)

    @filter.command("饥荒新玩家", alias={"dst新玩家"})
    async def cmd_new_player_alias(
        self, event: AstrMessageEvent, name: str = "", ku: str = ""
    ):
        """录入本服玩家 KU_ 对照"""
        async for result in self.cmd_new_player(event, name, ku):
            yield result

    @dst.command("新玩家", alias={"newplayer"})
    async def cmd_new_player(
        self, event: AstrMessageEvent, name: str = "", ku: str = ""
    ):
        """把昵称和 KU_ID 写进对照表，立刻用于在线和进出"""
        raw = self._rest_keyword(event)
        if not raw:
            raw = f"{name} {ku}".strip()
        try:
            nickname, userid = parse_bind_args(raw)
        except CliError as exc:
            yield self._md(event, f"**参数错误**\n\n{exc}")
            return
        if not nickname or not userid:
            nickname, userid = self._parse_name_ku(raw)
        if not nickname or not userid:
            yield self._md(
                event,
                "**用法**\n\n"
                "| 参数 | 示例 |\n"
                "| --- | --- |\n"
                "| 昵称 KU_ | `/饥荒新玩家 张三 KU_xxxxxxxx` |\n"
                "| 有空格 | `/饥荒新玩家 \"张三 2\" KU_xxxxxxxx` |\n\n"
                "昵称要和游戏里完全一致。",
            )
            return
        if not re.fullmatch(r"KU_[A-Za-z0-9]+", userid, re.I):
            yield self._md(
                event,
                f"KU_ID 格式不对：`{userid}`。应类似 `KU_xxxxxxxx`。",
            )
            return
        result = await self.store.add_ku_mapping(nickname, userid)
        if self.monitor.last_server and self.monitor.last_server.players:
            self.store.enrich_players(self.monitor.last_server.players)
        status = result["status"]
        shown = result["ku"]
        title = {"unchanged": "**已有对照**", "updated": "**已更新**"}.get(
            status, "**已录入**"
        )
        table = (
            "| 昵称 | KU_ |\n"
            "| --- | --- |\n"
            f"| {result['name'].replace('|', '｜')} | `{shown}` |"
        )
        extra = ""
        if status == "updated" and result["old_ku"]:
            extra = f"\n\n原对照：`{result['old_ku']}`"
        elif status == "added":
            extra = "\n\n下次进出和 `/饥荒状态` 会显示这个 KU_。"
        yield self._md(event, f"{title}\n\n{table}{extra}")

    @filter.command("饥荒物品", alias={"dst物品"})
    async def cmd_item_alias(self, event: AstrMessageEvent, keyword: str = ""):
        """检索物品 prefab"""
        async for result in self.cmd_item(event, keyword):
            yield result

    @dst.command("物品", alias={"item"})
    async def cmd_item(self, event: AstrMessageEvent, keyword: str = ""):
        """中文 / 英文 / prefab 互查物品 ID"""
        kw = self._rest_keyword(event, keyword)
        try:
            query, page = parse_search_args(kw)
        except CliError as exc:
            yield self._md(event, f"**参数错误**\n\n{exc}")
            return
        if not query:
            yield self._md(
                event,
                "**用法**\n\n"
                "| 参数 | 示例 |\n"
                "| --- | --- |\n"
                "| 关键词 | `/饥荒物品 金块` |\n"
                "| 翻页 | `/饥荒物品 金块 -p 2` |",
            )
            return
        source = self.catalog.search_items(query)
        pages = max(1, (len(source) + PAGE_SIZE - 1) // PAGE_SIZE) if source else 1
        if page > pages:
            yield self._md(event, page_overflow_text("饥荒物品", query, page, pages))
            return
        page_rows, page, pages, total = paginate(source, page)
        start = (page - 1) * PAGE_SIZE + 1
        text = format_items(page_rows, query, start=start)
        hint = page_hint("饥荒物品", query, page, pages, total)
        yield self._md(event, f"{text}\n\n{hint}" if hint else text)

    @filter.command("饥荒指令", alias={"dst指令"})
    async def cmd_command_alias(self, event: AstrMessageEvent, keyword: str = ""):
        """检索控制台指令"""
        async for result in self.cmd_command(event, keyword):
            yield result

    @dst.command("指令", alias={"cmd", "command"})
    async def cmd_command(self, event: AstrMessageEvent, keyword: str = ""):
        """检索饥荒控制台指令用法（不会代为执行）"""
        kw = self._rest_keyword(event, keyword)
        try:
            query, page = parse_search_args(kw)
        except CliError as exc:
            yield self._md(event, f"**参数错误**\n\n{exc}")
            return
        if not query:
            yield self._md(
                event,
                "**用法**\n\n"
                "| 参数 | 示例 |\n"
                "| --- | --- |\n"
                "| 关键词 | `/饥荒指令 封禁` |\n"
                "| 翻页 | `/饥荒指令 封禁 -p 2` |",
            )
            return
        source = self.catalog.search_commands(query)
        pages = max(1, (len(source) + PAGE_SIZE - 1) // PAGE_SIZE) if source else 1
        if page > pages:
            yield self._md(event, page_overflow_text("饥荒指令", query, page, pages))
            return
        page_rows, page, pages, total = paginate(source, page)
        start = (page - 1) * PAGE_SIZE + 1
        text = format_commands(page_rows, query, start=start)
        hint = page_hint("饥荒指令", query, page, pages, total)
        yield self._md(event, f"{text}\n\n{hint}" if hint else text)

    def _md(self, event: AstrMessageEvent, text: str):
        result = event.plain_result(text)
        if hasattr(result, "use_markdown"):
            result.use_markdown(True)
        return result

    def _help_result(self, event: AstrMessageEvent):
        return self._md(event, HELP_MARKDOWN)

    @filter.command("饥荒帮助", alias={"dst帮助", "饥荒菜单", "饥荒指令列表"})
    async def cmd_help_alias(self, event: AstrMessageEvent):
        """查看本机器人饥荒相关可用指令"""
        yield self._help_result(event)

    @dst.command("帮助", alias={"help", "菜单", "指令列表"})
    async def cmd_help(self, event: AstrMessageEvent):
        """查看本机器人饥荒相关可用指令"""
        yield self._help_result(event)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("饥荒订阅", alias={"dst订阅"})
    async def cmd_sub_alias(self, event: AstrMessageEvent):
        """管理员：订阅本会话的进出推送"""
        async for result in self.cmd_subscribe(event):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @dst.command("订阅", alias={"subscribe"})
    async def cmd_subscribe(self, event: AstrMessageEvent):
        """管理员：让机器人向当前群推送进出和开关服"""
        added = await self.store.add_subscription(event.unified_msg_origin)
        if added:
            yield self._md(event, "**已订阅**\n\n进出变化会推到这个会话。")
        else:
            yield self._md(event, "**已订阅**\n\n这个会话已经订阅过了。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("饥荒取消订阅", alias={"dst取消订阅"})
    async def cmd_unsub_alias(self, event: AstrMessageEvent):
        """管理员：取消本会话推送"""
        async for result in self.cmd_unsubscribe(event):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @dst.command("取消订阅", alias={"unsubscribe"})
    async def cmd_unsubscribe(self, event: AstrMessageEvent):
        """管理员：取消当前群的推送"""
        removed = await self.store.remove_subscription(event.unified_msg_origin)
        if removed:
            yield self._md(event, "**已取消订阅**")
        else:
            yield self._md(event, "**未订阅**\n\n这个会话本来就没有订阅。")
