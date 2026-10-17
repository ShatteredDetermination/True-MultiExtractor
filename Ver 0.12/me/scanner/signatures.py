# -*- coding: utf-8 -*-
"""引擎识别的二进制特征表。

表格驱动：想加一个引擎特征，往 ENGINE_SIGS 里加一行即可，不用改逻辑。
"""

# 引擎特征：(特征字节, 引擎键, 显示名)
# 越先匹配到的越优先，所以把最独特的放前面。
ENGINE_SIGS = (
    # --- 保护壳 / 打包器（放最前面，因为壳会盖住内层引擎） ---
    (b".enigma1", "enigma", "Enigma Virtual Box"),
    (b"Enigma Virtual Box", "enigma", "Enigma Virtual Box"),
    (b"PYZ-00.pyz", "pyinstaller", "PyInstaller"),
    (b"_MEIPASS", "pyinstaller", "PyInstaller"),

    # --- 游戏引擎 ---
    (b"YYRunnerInterface", "gamemaker", "GameMaker"),
    (b"YYGameMaker", "gamemaker", "GameMaker"),
    (b"yoyogames", "gamemaker", "GameMaker"),
    (b"data.win", "gamemaker", "GameMaker"),
    (b"il2cpp", "unity", "Unity (IL2CPP)"),
    (b"UnityPlayer", "unity", "Unity"),
    (b"unity.default", "unity", "Unity"),
    (b"GDPC", "godot", "Godot"),
    (b"Godot Engine", "godot", "Godot"),
    (b"godot", "godot", "Godot"),
    (b"CNCS", "clickteam", "Clickteam Fusion"),
    (b"Clickteam", "clickteam", "Clickteam Fusion"),
    (b"Fusion Runtime", "clickteam", "Clickteam Fusion"),
    # 注意：真实归档的魔数一律是 "RGSSAD\0" + 版本号字节
    # （.rgss2a / .rgss3a 的文件头里也写着 RGSSAD），
    # 所以这里不能按魔数猜 XP/VX/VX Ace —— 精确版本交给 rpgmaker 引擎去报。
    (b"RGSSAD", "rpgmaker", "RPG Maker"),
    (b"RGSS2A", "rpgmaker", "RPG Maker"),
    (b"RGSS3A", "rpgmaker", "RPG Maker"),
    (b"RGSS", "rpgmaker", "RPG Maker"),
    (b"app.asar", "electron", "Electron"),
    (b"nw.dll", "nwjs", "NW.js"),
    (b"UnrealEngine", "unreal", "Unreal Engine"),
    (b"libcocos", "cocos", "Cocos2d-x"),
    (b"love.dll", "love2d", "LOVE 2D"),
    (b"RenPy", "renpy", "Ren'Py"),
    (b"renpy", "renpy", "Ren'Py"),
    (b"libgdx", "libgdx", "libGDX"),
)

# 单独的 4 字节魔数（大文件里直接搜 ASCII 不方便，单列出来）
MAGIC_SIGS = (
    # Unreal .pak 魔数 0x5A6F12E1，小端存盘后是 E1 12 6F 5A
    (bytes.fromhex("E1126F5A"), "unreal", "Unreal Engine (.pak)"),
    # Unity 资源包
    (b"UnityFS", "unity", "Unity (AssetBundle)"),
    (b"UnityWeb", "unity", "Unity (AssetBundle)"),
    (b"UnityRaw", "unity", "Unity (AssetBundle)"),
)

# 目录结构特征：(必须存在的文件名, 可选限定条件, 引擎键, 显示名)
# 条件用简单的 "目录里还有另一个文件" 表达，交给 detect 处理。
DIR_MARKERS = (
    ("app.asar", (), "electron", "Electron"),
    ("data.win", (), "gamemaker", "GameMaker"),
    ("game.droid", (), "gamemaker", "GameMaker (Android)"),
    ("game.ios", (), "gamemaker", "GameMaker (iOS)"),
    ("game.unx", (), "gamemaker", "GameMaker (Linux)"),
    ("UnityPlayer.dll", (), "unity", "Unity"),
    ("project.godot", (), "godot", "Godot (工程形式)"),
    ("Data.ini", ("Modules",), "clickteam", "Clickteam Fusion"),
    ("Game.ini", (), "rpgmaker", "RPG Maker"),
    ("acsetup.cfg", (), "ags", "Adventure Game Studio"),
    ("data.dcp", (), "wintermute", "Wintermute"),
)

# 目录里出现某后缀就判定（后缀 -> 引擎）
DIR_EXT_MARKERS = (
    (".pck", "godot", "Godot (独立 PCK)"),
    (".rgssad", "rpgmaker", "RPG Maker (加密存档)"),
    (".rgss2a", "rpgmaker", "RPG Maker (加密存档)"),
    (".rgss3a", "rpgmaker", "RPG Maker (加密存档)"),
    (".rpa", "renpy", "Ren'Py (归档)"),
    (".pak", "unreal", "Unreal Engine"),
    (".assets", "unity", "Unity"),
    (".bundle", "unity", "Unity (AssetBundle)"),
)
