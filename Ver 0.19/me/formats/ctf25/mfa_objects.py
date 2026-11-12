# -*- coding: utf-8 -*-
"""MFA 帧内对象定义（CObjectInfo）与实例（FrameInstance）的写手 —— V0.19 实验性。

字段顺序来自公开的 MFA 结构文档，并用参考样本 `Platformer Engine.mfa` 的
`Level` 帧核对过对象头（type=2 / handle=0 / name=`Enemy` / 透明=1 / 图标=16 /
标志=0x20）。**加载器（各类型的私有段）只写「空壳」**：

* Active：没有动画表（`u8 0`）；
* String：宽高 0、字体 0、颜色 0、0 段；
* Counter：值 0、显示为数字、0 张图；
* 扩展对象（类型 >= 32）**不写** —— 扩展的 name / magic / subType 都在报错边缘，
  没有样本可核对，不猜。

所以这一层出来的对象是「有定义、没内容」的占位对象：编辑器里能看到对象与摆位，
但动画、文字、计数器外观都是默认值。文档与报告里必须这么写，不许说成「完整还原」。

实例布局（28 B）来自公开文档并与 CCN 侧 20 B 实例一一对应：
``i32 x / i32 y / u32 图层 / u32 句柄 / u16 标志 / i16 实例值 / u32 ParentType /
u32 ObjectInfo / u32 ParentHandle``。
"""

import struct

from me.formats.ctf25 import mfa

#: 帧内对象列表里的对象标志默认值（0x20 = 编辑器同名同类型同步）
OBJECT_FLAGS = 0x20

#: 加载器里的对象标志位（按 Nebula 的位序）
LOADER_FLAG_DISPLAY_IN_FRONT = 1 << 0
LOADER_FLAG_HAS_ALTERABLES = 1 << 8
LOADER_FLAG_HAS_TEXT = 1 << 16
LOADER_FLAG_CREATE_AT_START = 1 << 17

#: 新对象标志：可见（bit3）
NEW_FLAG_VISIBLE_AT_START = 1 << 3

SUPPORTED_TYPES = (2, 3, 7)

#: CCN 属性段开头的固定头长度（跳过 4 字节长度前缀后）
COMMON_HEADER = 60


def parse_object_common(blob):
    """解 CCN 对象属性段的固定头（`0x2256` 里每段解压后的前 60 B）。

    布局（Fusion 2.5，编译号 ≥ 284）：6 个 u16 段偏移 / u32 对象标志 /
    8 个 i16 限定符 / u16 数据偏移 / u16 可改变量偏移 / u16 可改字符串偏移 /
    u16 新对象标志 / u16 偏好标志 / 4 B 标识（`SPRI`/`TEXT`/`CNTR`/`0INI`…）/
    4 B 背景色 / i32 转入偏移 / i32 转出偏移 / u16 可改变量名偏移。
    解不出来返回 None（段太短或对不上）。
    """
    if not blob or len(blob) < COMMON_HEADER + 4:
        return None
    body = blob[4:]           # 段内前 4 B 是它自己的长度
    try:
        offsets = list(struct.unpack_from("<6H", body, 0))
        flags = struct.unpack_from("<I", body, 12)[0]
        qualifiers = list(struct.unpack_from("<8h", body, 16))
        data_off, values_off, strings_off = struct.unpack_from("<3H", body, 32)
        new_flags = struct.unpack_from("<H", body, 38)[0]
        pref_flags = struct.unpack_from("<H", body, 40)[0]
        identifier = body[42:46]
        back_color = body[46:50]
        trans_in, trans_out = struct.unpack_from("<ii", body, 50)
        names_off = struct.unpack_from("<H", body, 58)[0]
    except struct.error:
        return None
    if not all(32 <= b < 127 for b in identifier):
        return None
    return {
        "offsets": offsets, "flags": flags, "qualifiers": qualifiers,
        "data_offset": data_off, "values_offset": values_off,
        "strings_offset": strings_off, "new_flags": new_flags,
        "pref_flags": pref_flags, "identifier": identifier.decode("latin-1"),
        "back_color": back_color, "trans_in": trans_in, "trans_out": trans_out,
        "names_offset": names_off,
    }


def loader_flags(common):
    """把 CCN 的对象标志翻成 MFA 加载器的对象标志。

    bit17 含义相反（CCN 是「开始时不要创建」，MFA 是「开始时创建」），
    bit18（CCNCheck）是 CCN 专用，MFA 里必须清掉。
    """
    flags = int(common.get("flags", 0))
    flags &= ~(1 << 18)
    if flags & (1 << 17):
        flags &= ~(1 << 17)
    else:
        flags |= (1 << 17)
    return flags


def _loader_base(object_flags, common=None):
    """MFAObjectLoader 的公共段：两套 flags + 背景色 + 9 个限定符 + 4 个空表 + 2 个转场开关。"""
    common = common or {}
    w = mfa.Writer()
    w.u32(object_flags)
    w.u32(int(common.get("new_flags", 0)) or NEW_FLAG_VISIBLE_AT_START)
    w.raw(bytes(common.get("back_color", b"\x00\x00\x00\x00")))
    quals = list(common.get("qualifiers") or [])
    for i in range(8):
        w.raw(struct.pack("<h", int(quals[i]) if i < len(quals) else -1))
    w.raw(struct.pack("<h", -1))          # 收尾 -1
    for _ in range(4):
        w.u32(0)                          # 可改变量 / 可改字符串 / 运动 / 行为
    w.raw(b"\x00")                        # 无转入场
    w.raw(b"\x00")                        # 无转出场
    return w.getvalue()


def build_loader(type_id, text=None, common=None):
    """按类型写私有段；不支持的返回 None。

    ``text`` 只对 String（类型 3）有意义：写成一段（CString 正文 + u32 flags）。
    其余属性仍是占位默认值。
    """
    if type_id == 2:
        flags = (loader_flags(common) if common else 0) or \
            (LOADER_FLAG_DISPLAY_IN_FRONT | LOADER_FLAG_CREATE_AT_START)
        return _loader_base(flags, common) + b"\x00"    # 0 条动画
    if type_id == 3:
        flags = (loader_flags(common) if common else 0) or \
            (LOADER_FLAG_HAS_TEXT | LOADER_FLAG_CREATE_AT_START)
        body = _loader_base(flags, common)
        w = mfa.Writer()
        w.i32(0)                          # 宽
        w.i32(0)                          # 高
        w.i32(0)                          # 字体
        w.raw(b"\x00\x00\x00\x00")        # 颜色
        w.u32(0)                          # 对齐等标志
        w.i32(0)                          # 立体
        if text:
            w.u32(1)                      # 1 段
            w.cstring(text)
            w.u32(0)                      # 段落 flags
        else:
            w.u32(0)                      # 0 段
        return body + w.getvalue()
    if type_id == 7:
        flags = (loader_flags(common) if common else 0) or \
            (LOADER_FLAG_CREATE_AT_START | LOADER_FLAG_HAS_TEXT)
        body = _loader_base(flags, common)
        w = mfa.Writer()
        w.i32(0)                          # 值
        w.i32(0)                          # 最小
        w.i32(0)                          # 最大
        w.u32(1)                          # 显示类型：数字
        w.i32(1)                          # 填充：单色
        w.raw(b"\x00\x00\x00\x00")        # 颜色 1
        w.raw(b"\x00\x00\x00\x00")        # 颜色 2
        w.i32(0)                          # 竖向渐变
        w.i32(0)                          # 条方向
        w.i32(0)                          # 宽
        w.i32(0)                          # 高
        w.u32(0)                          # 0 张图
        w.u32(0)                          # 字体
        return body + w.getvalue()
    return None


def build_object_info(type_id, handle, name, icon_handle=0, text=None,
                      common=None):
    """一条 CObjectInfo；类型不支持时返回 None。"""
    loader = build_loader(type_id, text=text, common=common)
    if loader is None:
        return None
    w = mfa.Writer()
    w.i32(type_id)
    w.i32(handle)
    w.cstring(name or "")
    w.i32(0)                              # 透明
    w.i32(0)                              # 墨迹效果
    w.u32(0)                              # 墨迹参数
    w.i32(0)                              # 抗锯齿
    w.u32(OBJECT_FLAGS)
    w.i32(1)                              # 图标类型（不是 1 编辑器会拒绝打开）
    w.u32(icon_handle)
    w.raw(b"\x00")                        # 对象内的 chunk 列表收尾
    return w.getvalue() + loader


def build_folders(handles):
    """每个对象一个隐藏文件夹（与参考实现一致）：`u16 112 / u8 0 / u8 3 / i32 子对象`。"""
    w = mfa.Writer()
    w.u32(len(handles))
    for handle in handles:
        w.raw(struct.pack("<H", 112))
        w.raw(b"\x00")
        w.raw(b"\x03")                    # 隐藏文件夹
        w.i32(handle)
    return w.getvalue()


def build_instance(object_handle, x, y, layer, handle, instance_value=0):
    """一条 28 B 的实例记录。"""
    w = mfa.Writer()
    w.i32(x)
    w.i32(y)
    w.u32(layer)
    w.u32(handle)
    w.raw(struct.pack("<H", 0))           # 实例标志
    w.raw(struct.pack("<h", instance_value))
    w.u32(0)                              # ParentType
    w.u32(object_handle)                  # ObjectInfo
    w.u32(0)                              # ParentHandle
    return w.getvalue()


def split_rest(rest):
    """把模板帧体拆成「事件段」和「帧尾 chunk 列表」两段。

    模板帧体开头 8 B 是文件夹数 / 实例数（都是 0），中间是事件段（空事件也有
    一小段结构），后面才是 chunk 列表。占位对象写进帧体时要保留后两段原样。
    """
    chunks = mfa.frame_chunks(rest)
    if not chunks:
        raise mfa.MfaError("模板帧体里找不到帧尾 chunk 列表")
    start = chunks[0][1]
    if start < 8:
        raise mfa.MfaError("模板帧体太短，拆不出事件段")
    return rest[8:start], rest[start:]


def build_frame_objects(instances, texts=None, commons=None):
    """按一帧的实例表写对象定义与实例，返回 (对象块, 句柄, 实例块, 跳过的实例数)。

    ``texts`` 是 ``{对象下标: 正文}``（String 对象用），没有就写空段落。
    """
    texts = texts or {}
    commons = commons or {}
    order = []
    seen = set()
    for inst in instances:
        if inst.obj_index in seen:
            continue
        seen.add(inst.obj_index)
        order.append(inst)

    blobs = []
    handles = []
    mapping = {}
    for inst in order:
        blob = build_object_info(inst.object_type, inst.obj_index,
                                 inst.object_name,
                                 text=texts.get(inst.obj_index),
                                 common=commons.get(inst.obj_index))
        if blob is None:
            continue
        blobs.append(blob)
        handles.append(inst.obj_index)
        mapping[inst.obj_index] = inst.object_name

    inst_blobs = []
    skipped = 0
    for inst in instances:
        if inst.obj_index not in mapping:
            skipped += 1
            continue
        inst_blobs.append(build_instance(
            inst.obj_index, inst.x, inst.y, inst.layer, len(inst_blobs)))
    return blobs, handles, inst_blobs, skipped


def build_frame_rest(blobs, handles, inst_blobs, events_raw, chunks_raw):
    """拼帧体：对象列表 + 文件夹 + 实例列表 + 事件段 + 帧尾 chunk 列表。

    对象数不写在这里 —— 它在帧头（`build_frame` 写 `f.object_count`），
    帧体直接从第一个 CObjectInfo 开始。
    """
    w = mfa.Writer()
    # 对象数在帧头里（build_frame 会写），帧体本身从第一个对象开始，别再写一遍
    for blob in blobs:
        w.raw(blob)
    w.raw(build_folders(handles))
    w.u32(len(inst_blobs))
    for blob in inst_blobs:
        w.raw(blob)
    w.raw(events_raw)
    w.raw(chunks_raw)
    return w.getvalue()


def add_objects_to_frame(frame, specs):
    """往 ``MfaFrame`` 里塞对象与实例，返回 ``(写了几个, 跳过了几个)``。

    ``specs`` 是 ``[(类型, 句柄, 名字, x, y, 图层[, 正文])]``。只写支持的类型
    （2/3/7），扩展对象跳过。帧体里的对象列表 / 文件夹 / 实例列表会重建，
    事件段与帧尾 chunk 列表（含虚拟矩形）保持原样。
    """
    events_raw, chunks_raw = split_rest(frame.rest_raw)
    blobs = []
    handles = []
    inst_blobs = []
    skipped = 0
    for spec in specs:
        type_id, handle, name, x, y, layer = spec[:6]
        text = spec[6] if len(spec) > 6 else None
        blob = build_object_info(type_id, handle, name, text=text)
        if blob is None:
            skipped += 1
            continue
        blobs.append(blob)
        handles.append(handle)
        inst_blobs.append(build_instance(handle, x, y, layer, len(inst_blobs)))
    frame.rest_raw = build_frame_rest(blobs, handles, inst_blobs,
                                      events_raw, chunks_raw)
    frame.object_count = len(blobs)
    return len(blobs), skipped
