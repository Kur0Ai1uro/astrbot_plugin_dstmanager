# 饥荒助手

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件：通过 Klei 公共大厅监测饥荒联机版（Don't Starve Together）专用服务器，推送进出与开关服，并检索物品 prefab、本服玩家 `KU_`、控制台指令用法。

不需要在游戏里加模组，机器人和游戏服务器也不必在同一台机器。

- 需要 **AstrBot ≥ 4.0.0**
- 依赖：`aiohttp>=3.9.0`（见 `requirements.txt`）

## 功能

- 轮询大厅，查看房间是否公开、人数、季节、模式
- 管理员订阅后，推送玩家进入 / 离开，以及房间出现在大厅 / 离开大厅
- `/饥荒状态` 用 Markdown 表格展示房间和当前在线（`/饥荒在线` 为同一指令）
- `/饥荒玩家` 列出进过本服的人，也可按昵称或 `KU_` 检索；用 `/饥荒新玩家` 补全对照
- 物品 prefab 检索，并给出 `c_give` 示例
- 控制台指令模糊检索（如 `cgive`、`godmde`），**只给用法，不会代为执行**
- 空服时过滤专用服务器的占位玩家，避免显示成「未知 / 未选角色」
- 不展示 SteamID 和服务器地址

物品与指令条目整理自 [Don't Starve Wiki](https://dontstarve.wiki.gg/) 等公开资料，收录常用原版与官方 DLC，不含创意工坊模组。

## 安装



### WebUI 导入（推荐）

1. 用仓库里的 `python pack.py` 生成 `astrbot_plugin_dst.zip`，或从 Release 下载。
2. AstrBot WebUI → **插件** → **+** → **文件上传**。
3. 选择 `astrbot_plugin_dst.zip`，不要先解压再传散文件。
4. 打开插件配置，填写房间名；要看谁进谁出再填 Token。
5. 若提示缺少 `aiohttp`，在插件页安装依赖，或重载一次让它读取 `requirements.txt`。

zip 的根目录必须是 `astrbot_plugin_dst/`，里面直接是 `main.py` 和 `metadata.yaml`。用资源管理器「全选文件再压缩」容易打成扁平包，旧版 WebUI 会报 `NotADirectoryError`。

### 手动放置

把本仓库（或解压后的 `astrbot_plugin_dst` 文件夹）放到 AstrBot 的 `data/plugins/` 下，重载插件。

## 配置

在 AstrBot 插件卡片里填写，保存后重载或等下一轮轮询即可。


| 配置项            | 说明                                           |
| -------------- | -------------------------------------------- |
| 房间名称           | 大厅里显示的名字，支持包含匹配                              |
| 服务器 IP / 端口    | 可选。房间名容易重复时用来精确匹配。端口填 Master 端口，`0` 表示不按端口过滤 |
| 大厅地区           | 国内 Steam 云服可先试 `ap-east-1`，不确定选 `auto`       |
| 平台             | Steam 选 `Steam`；WeGame 选 `Rail`              |
| Klei Token     | `cluster_token.txt` 全文。不填也能查人数和季节            |
| 轮询间隔           | 默认 45 秒，建议 30～90，最小按 15 秒处理                  |
| 连续找不到几次算离线     | 默认 3，避免大厅偶发漏房间时误报                            |
| HTTP 代理        | 访问大厅很慢或失败时再填，例如 `http://127.0.0.1:7890`      |
| 推送玩家进出         | 默认开启                                         |
| 推送服务器上线 / 离开大厅 | 默认开启                                         |




## 获取 Token

大厅列表只能看到人数和季节。要看到**谁在线上、谁进谁出**，必须填写开服时用的 Klei Token。它就是专用服务器目录里的 `cluster_token.txt`，不是 Steam / 面板登录令牌。

**不要把 Token 发到群里或提交到 Git 仓库。**

### 从已有存档复制

房间已经能在游戏大厅搜到的话，Token 已经写在存档里了。

常见路径：

- `DoNotStarveTogether/Cluster_1/cluster_token.txt`
- `DoNotStarveTogether/config/Cluster_1/cluster_token.txt`
- `DoNotStarveTogether/config/server/cluster_token.txt`

糖糕云等面板：实例控制台 → 房间设置里的「Token / 服务器令牌」，或文件管理器搜索 `cluster_token.txt`。打开后通常只有一行，从头到尾完整复制，不要多空行。

如果你不会进后台，那也很简单：  
下载服务器的备份存档，一般为zip格式；在存档的根目录下就存有`cluster_token.txt`

### 还没有 Token 时

1. 打开 [Klei 专用服务器 Token 页](https://accounts.klei.com/account/game/servers?game=DontStarveTogether)。
2. 用 Steam 登录，必要时先链接账户。
3. 添加新服务器，复制 Server Token。
4. 同时写入服务器的 `cluster_token.txt` 和本插件配置，并重启专用服务器。

国内打不开该页面时，换网络或开加速器后再试。


| 功能            | 不填 Token | 填了 Token             |
| ------------- | -------- | -------------------- |
| 房间是否在大厅、人数、季节 | 可以       | 可以                   |
| 当前玩家昵称、角色     | 不行       | 可以                   |
| 谁进谁出          | 只能报人数变化  | 可以报到人                |
| 本服 `KU_`      | 需自行录入对照  | 大厅通常仍不给 `KU_`，对照方式相同 |


Token 填错或过期时，状态指令会提示原因，插件不会崩溃。

## 指令

群里发送 `/饥荒帮助` 可查看同一份说明。英文别名把「饥荒」换成 `dst`，例如 `/dst状态`。`/饥荒菜单`、`/饥荒指令列表` 等同帮助。


| 指令                    | 权限  | 说明                             |
| --------------------- | --- | ------------------------------ |
| `/饥荒帮助`               | 所有人 | 列出可用指令                         |
| `/饥荒状态`               | 所有人 | 房间状态与当前在线。`/饥荒在线` 为同一指令        |
| `/饥荒玩家`               | 所有人 | 全部历史玩家。翻页：`/饥荒玩家 2`             |
| `/饥荒玩家 <关键词>`         | 所有人 | 检索本服历史。超过 5 条时加页码，如 `/饥荒玩家 张三 2` |
| `/饥荒新玩家 <昵称> <KU_ID>` | 所有人 | 补全对照，如 `/饥荒新玩家 张三 KU_xxxxxxxx` |
| `/饥荒物品 <关键词>`         | 所有人 | 查 prefab 与 `c_give`。下一页加页码     |
| `/饥荒指令 <关键词>`         | 所有人 | 模糊检索控制台用法，不执行                  |
| `/饥荒订阅`               | 管理员 | 把进出推送到当前会话                     |
| `/饥荒取消订阅`             | 管理员 | 取消推送                           |


未订阅时仍可用查询指令。只有订阅后才会推送进出。

进出推送示例：

```text
[饥荒] 张三 进入服务器（威尔逊 / KU_xxxx，2/6）
当前在线 2/6

| 序号 | 昵称 | 角色 | KU_ |
| ---: | --- | --- | --- |
| 1 | 张三 | 威尔逊 | KU_xxxx |
| 2 | 李四 | 薇洛 | KU_yyyy |
```



## KU_ 对照

Klei 大厅名单一般只有昵称和角色，没有 `KU_`。可以用下面任一方式补全：

1. `/饥荒新玩家 昵称 KU_xxxxxxxx`（立刻生效，不必重载）
2. 把服务器存档里的 `player.txt`（`KU_xxxx=昵称`）放到 AstrBot 的 `data/plugin_data/astrbot_plugin_dst/player.txt`，然后重载

对照和玩家历史写在 `data/plugin_data/astrbot_plugin_dst/`，更新插件不会丢失。插件包本身不携带任何服务器玩家数据。

新玩家进服后若尚未录入，推送里会提示录入指令。

## 注意事项

- 只监测出现在 Steam / Rail **公共大厅** 的房间。局域网、仅好友、未成功上大厅的服查不到。
- 进出靠轮询对比名单，进了又马上走的人可能漏报。
- 最后一人离开、空房占位玩家等边界情况已按大厅空名单处理；若大厅 CDN 延迟，仍可能慢一轮。
- 控制台检索没有任何远程执行服务器指令的能力。
- 访问 Klei 大厅超时会记警告并下一轮重试，不会让监测循环崩溃。可在配置里填 HTTP 代理。

## 打包

```bash
python pack.py
```

会在本目录生成可供 WebUI 上传的 `astrbot_plugin_dst.zip`。

## 致谢

大厅接口用法参考了社区项目（如 dstgo/wilson、DstServerQuery 以及各类 DST 大厅查询工具）。指令与物品说明参考 Don't Starve Wiki。

## 贡献

欢迎 Issue 和 Pull Request。提交前请不要把 `cluster_token.txt`、真实 `KU_` 或存档文件打进仓库。

## 许可证

本项目采用 [MIT License](LICENSE) 开源。