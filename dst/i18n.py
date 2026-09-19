"""大厅字段和角色 prefab 的中文对照。"""

CHARACTERS: dict[str, str] = {
    "wilson": "威尔逊",
    "willow": "薇洛",
    "wolfgang": "沃尔夫冈",
    "wendy": "温蒂",
    "wx78": "WX-78",
    "wickerbottom": "薇克巴顿",
    "woodie": "伍迪",
    "wes": "韦斯",
    "waxwell": "麦斯威尔",
    "wathgrithr": "薇格弗德",
    "webber": "韦伯",
    "winona": "薇诺娜",
    "warly": "沃利",
    "wortox": "沃拓克斯",
    "wormwood": "沃姆伍德",
    "wurt": "沃特",
    "walter": "沃尔特",
    "wanda": "旺达",
    "wonkey": "芜猴",
}

SEASONS: dict[str, str] = {
    "autumn": "秋天",
    "winter": "冬天",
    "spring": "春天",
    "summer": "夏天",
}

MODES: dict[str, str] = {
    "survival": "生存",
    "wilderness": "荒野",
    "endless": "无尽",
    "lavaarena": "熔炉",
    "quagmire": "暴食",
}

INTENTS: dict[str, str] = {
    "social": "社交",
    "coop": "合作",
    "competitive": "竞争",
    "madness": "疯狂",
    "relaxed": "轻松",
}


def character_name(prefab: str) -> str:
    key = (prefab or "").strip().lower()
    if not key:
        return "未选角色"
    return CHARACTERS.get(key, prefab)


def season_name(season: str) -> str:
    key = (season or "").strip().lower()
    return SEASONS.get(key, season or "未知")


def mode_name(mode: str) -> str:
    key = (mode or "").strip().lower()
    return MODES.get(key, mode or "未知")


def intent_name(intent: str) -> str:
    key = (intent or "").strip().lower()
    return INTENTS.get(key, intent or "未知")
