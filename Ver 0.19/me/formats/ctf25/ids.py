# -*- coding: utf-8 -*-
"""CTF chunk 编号 -> 名字。

表来自 Clickteam Fusion 2.5 文件格式的公开逆向资料（CTFAK 的 ChunkList）。
我们只用其中一小部分（图像库、音频库、字体库、应用信息），
但整张表留着方便以后排查未知 chunk。
"""

CHUNK_NAMES = {
    4386: "Preview",
    8738: "Mini-Header",
    8739: "Header",
    8740: "Name",
    8741: "Author",
    8742: "Menu",
    8743: "Extension Path",
    8744: "Extensions",
    8745: "Frame Items",
    8746: "Global Events",
    8747: "Frame Handles",
    8748: "Extension Data",
    8749: "Additional Extension",
    8750: "App Editor-Filename",
    8751: "App Target-Filename",
    8752: "App Docs",
    8753: "Other Extensions",
    8754: "Global Values",
    8755: "Global Strings",
    8756: "Extension List",
    8757: "Icon 16x16",
    8758: "Is Demo",
    8759: "Serial Number",
    8760: "Binary Files",
    8761: "Menu Images",
    8762: "About Text",
    8763: "Copyright",
    8764: "Global Value Names",
    8765: "Global String Names",
    8766: "Movement Extensions",
    8767: "Frame Items 2",
    8768: "Exe Only",
    8770: "Protection",
    8771: "Shaders",
    8773: "Extended Header",
    8774: "Spacer",
    8775: "Frame Offset",
    8776: "Ad Mob ID",
    8779: "Android Menu",
    8787: "2.5+ Object Headers",
    8788: "2.5+ Object Names",
    8789: "2.5+ Object Shaders",
    8790: "2.5+ Object Properties",
    8792: "Font Info",
    8793: "Fonts",
    8794: "Shaders",
    13107: "Frame",
    13108: "Frame Header",
    13109: "Frame Name",
    13110: "Frame Password",
    13111: "Frame Palette",
    13112: "Frame Item Instances",
    13117: "Frame Events",
    13121: "Frame Layers",
    13122: "Frame Virtual Rect",
    13125: "Frame Layer Effects",
    17476: "Object Info Header",
    17477: "Object Info Name",
    17478: "Object Common",
    17480: "Object Effects",
    17664: "Object Shapes",
    21845: "Image Handles",
    21846: "Font Handles",
    21847: "Sound Handles",
    21848: "Music Handles",
    26213: "Bank Offsets",
    26214: "Images",
    26215: "Fonts",
    26216: "Sounds",
    26217: "Music",
    32494: "Fusion 3 Seed",
    32639: "Last Chunk",
}

# 我们关心的 chunk
CHUNK_APP_HEADER = 8739          # 0x2223
CHUNK_APP_NAME = 8740            # 0x2224
CHUNK_APP_AUTHOR = 8741          # 0x2225
CHUNK_APP_COPYRIGHT = 8763       # 0x223B
CHUNK_25_OBJECT_HEADERS = 8787   # 0x2253 出现即说明是 2.5+
CHUNK_FONT_INFO = 8792           # 0x2258
CHUNK_FONTS_2 = 8793             # 0x2259
CHUNK_IMAGES = 26214             # 0x6666
CHUNK_FONTS = 26215              # 0x6667
CHUNK_SOUNDS = 26216             # 0x6668
CHUNK_MUSIC = 26217              # 0x6669
CHUNK_FUSION3_SEED = 32494       # 0x7EEE
CHUNK_LAST = 32639               # 0x7F7F


def name_of(chunk_id: int) -> str:
    return CHUNK_NAMES.get(chunk_id, f"未知-{chunk_id}")
