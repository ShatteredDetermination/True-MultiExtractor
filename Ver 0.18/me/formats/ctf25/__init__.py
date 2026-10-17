# -*- coding: utf-8 -*-
"""Clickteam Fusion 2.5 文件格式解析（纯 Python）。

拆成多个小文件，每个只管一件事：

    binary.py     底层字节读取
    ids.py        chunk 编号 -> 名字
    lz4.py        LZ4 block 解压（图像数据用）
    container.py  在一个文件里定位 CCN 数据流
    chunks.py     chunk 流遍历
    imagebank.py  图像条目解析
    imagecodec.py 图像像素格式 -> RGBA
    soundbank.py  音频条目解析
    fontbank.py   字体条目解析
    extractor.py  对外入口：把一个 CTF 文件拆成资源
"""
