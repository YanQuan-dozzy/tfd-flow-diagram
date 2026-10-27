#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TFD（业务流程图 / Transaction Flow Diagram）SVG 生成器。

用法:
    python tfd.py spec.json -o out.svg

设计要点:
  * 网格坐标系：节点用 (col,row) 摆放，脚本自动对齐行列 —— 手绘风格整齐的关键
  * 五类 TFD 符号：unit(业务处理单位/部门) / proc(业务处理功能) /
    doc(各类单证、报表) / store(数据存储、档案) / card(卡片、收集资料)
  * 正交走线：按 fromSide / toSide 生成直角折线，箭头自动缩进贴边框
  * 全文纯标准库，无第三方依赖
"""

import argparse
import json
import os
import sys
import unicodedata

# ---------------------------------------------------------------- 基础工具

TYPE_ALIASES = {
    "unit": "unit", "extern": "unit", "entity": "unit", "role": "unit",
    "person": "unit", "部门": "unit", "单位": "unit", "人员": "unit",
    "外部实体": "unit", "业务处理单位": "unit",
    "proc": "proc", "process": "proc", "处理": "proc", "功能": "proc",
    "业务处理功能": "proc", "action": "proc",
    "doc": "doc", "document": "doc", "单据": "doc", "报表": "doc",
    "单证": "doc", "各类单证、报表": "doc",
    "store": "store", "storage": "store", "file": "store", "存储": "store",
    "数据存储": "store", "文档": "store", "档案": "store", "文件": "store",
    "card": "card", "卡片": "card", "收集资料": "card",
}

SIDE_ALIASES = {
    "t": "top", "top": "top", "上": "top",
    "b": "bottom", "bottom": "bottom", "下": "bottom",
    "l": "left", "left": "left", "左": "left",
    "r": "right", "right": "right", "右": "right",
}

FONT_STACK = ("'Microsoft YaHei','PingFang SC','Hiragino Sans GB',"
              "'Noto Sans SC','Source Han Sans SC',sans-serif")

PALETTE = {
    "textbook": {
        "bg": "#ffffff", "stroke": "#222222", "text": "#111111",
        "edge": "#222222", "muted": "#555555", "fold": "#ffffff",
        "fill": {"unit": "#ffffff", "proc": "#ffffff", "doc": "#ffffff",
                 "store": "#ffffff", "card": "#ffffff"},
    },
    "soft": {
        "bg": "#ffffff", "stroke": "#33475b", "text": "#1f2328",
        "edge": "#3d4b5c", "muted": "#5a6b7c", "fold": "#f0dcbb",
        "fill": {"unit": "#e9f0fb", "proc": "#ffffff", "doc": "#fdf3e2",
                 "store": "#e8f4ec", "card": "#f2eefb"},
    },
}

DOC_STYLE = "wave"        # wave：波浪底边（样本画法，默认）｜ fold：折角纸张



def is_wide(ch):
    return unicodedata.east_asian_width(ch) in ("W", "F")


def text_width(s, fs):
    """粗估一段文字的渲染宽度（px）。"""
    w = 0.0
    for ch in s:
        if ch == "\t":
            w += fs * 1.6
        elif is_wide(ch):
            w += fs
        elif ch == " ":
            w += fs * 0.30
        elif ch.isdigit():
            w += fs * 0.56
        elif "A" <= ch <= "Z":
            w += fs * 0.65
        elif ch in "ilm.,:;'|!":
            w += fs * 0.30
        else:
            w += fs * 0.56
    return w


BREAK_AFTER = "并及和与、，；。： "


def _best_split(s, fs, max_w):
    """在"并/及/和/与/、/，"这类语义断点后断开，并让两行长度尽量均衡。"""
    best = None
    for i in range(1, len(s)):
        left, right = s[:i].rstrip(), s[i:].lstrip()
        if not left or not right:
            continue
        if text_width(left, fs) > max_w or text_width(right, fs) > max_w:
            continue
        score = abs(len(left) - len(right))
        if s[i - 1] in "、，；。： " or s[i] in "并及和与":
            score -= 1.6                 # 语义断点更自然（连词放到下一行开头）
        if len(right) == 1:
            score += 6.0                 # 末行只剩一个字，很难看
        if best is None or score < best[0]:
            best = (score, left, right)
    return (best[1], best[2]) if best else None


def wrap_text(text, max_w, fs):
    """折行：优先在语义断点均衡地折成两行，其次才按宽度贪心切。"""
    lines = []
    for raw in str(text).split("\n"):
        raw = raw.strip()
        if not raw:
            lines.append("")
            continue
        if text_width(raw, fs) <= max_w or len(raw) == 1:
            lines.append(raw)
            continue
        two = _best_split(raw, fs, max_w)
        if two:
            lines.extend(two)
            continue
        cur = ""
        for ch in raw:
            if text_width(cur + ch, fs) <= max_w or not cur:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
    return lines or [""]


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def num(v):
    """紧凑输出数字，避免 12.0000000001 这类噪声。"""
    if abs(v - round(v)) < 1e-6:
        return str(int(round(v)))
    return f"{v:.1f}"


# ---------------------------------------------------------------- 规格解析

class Node(object):
    __slots__ = ("id", "type", "label", "col", "row", "w", "h", "fs",
                 "lines", "x", "y", "span", "note", "explicit")

    def __init__(self, raw, defaults):
        self.id = str(raw.get("id") or raw.get("name") or "").strip()
        if not self.id:
            raise ValueError("节点缺少 id: %r" % (raw,))
        t = str(raw.get("type") or raw.get("t") or "proc")
        self.type = TYPE_ALIASES.get(t.lower(), TYPE_ALIASES.get(t, None))
        if self.type is None:
            raise ValueError("节点 %s 的类型 %r 无法识别（unit/proc/doc/store/card）"
                             % (self.id, t))
        self.label = raw.get("label") or raw.get("l") or raw.get("text") or self.id
        self.col = raw.get("col", raw.get("c"))
        self.row = raw.get("row", raw.get("r"))
        self.span = int(raw.get("span", 1) or 1)
        self.fs = float(raw.get("fs") or defaults["fs"])
        self.note = raw.get("note")
        pad = raw.get("pad")
        pad = float(pad) if pad is not None else defaults["pad"]
        # 文字先按上限宽度折行，再据此确定形状尺寸
        tab_allow = 14.0 if self.type == "store" else 0.0
        max_line_w = float(raw.get("maxw") or defaults["max_node_w"]) - 2 * pad - tab_allow
        min_fs = float(defaults.get("min_fs", 11))
        self.lines = wrap_text(self.label, max_line_w, self.fs)
        while len(self.lines) > 2 and self.fs > min_fs:
            self.fs -= 1.0
            self.lines = wrap_text(self.label, max_line_w, self.fs)
        plain = str(self.label).replace("\n", "")
        if len(plain) > 12:
            WARNINGS.append(
                "标签过长: %s（%d 字）——节点里只写名称或动作短语，"
                "不要写成整句说明" % (plain, len(plain)))
        if self.type == "unit" and "\n" not in str(self.label) and len(self.lines) == 1 \
                and text_width(self.lines[0], self.fs) > 62:
            # 单位符号在标准里画成正圆；标签过长时折成两行，避免拉成扁椭圆
            s = self.lines[0]
            k = len(s) // 2
            self.lines = [s[:k], s[k:]]
        self._measure(raw, defaults, pad)

    def _measure(self, raw, defaults, pad):
        lh = self.fs * defaults["line_height"]
        tw = max(text_width(s, self.fs) for s in self.lines)
        th = lh * len(self.lines)

        min_w = {"unit": 68.0, "proc": 78.0, "doc": 86.0, "store": 84.0,
                 "card": 80.0}[self.type]
        if self.type == "unit":
            mode = str(defaults.get("unit_shape", "auto")).lower()
            if not (raw.get("w") or raw.get("h")):
                d = max(46.0, tw * 1.45 + 14, th * 1.8 + 10)
                if mode == "circle" or (mode == "auto" and d <= 78):
                    # 教材范例：采购员/库工/主任这类短标签画成正圆
                    self.explicit = False
                    self.w = self.h = d
                    self.x = self.y = 0.0
                    return
                # 长标签（如"有关部门"）放宽成横椭圆
                auto_w = max(d, tw * 1.5 + 16)
                auto_h = max(46.0, th * 1.75 + 12)
            else:
                auto_w = tw * 1.55 + 16
                auto_h = th * 1.75 + 14
        elif self.type == "doc":
            auto_w = tw + 2 * pad + 6
            auto_h = th + 2 * pad + 9
        elif self.type == "store":
            auto_w = tw + 2 * pad + 24          # 左格占掉的宽度也要算进来
            auto_h = th + 2 * pad + 2
        else:
            auto_w = tw + 2 * pad
            auto_h = th + 2 * pad

        self.explicit = bool(raw.get("w") or raw.get("h"))
        self.w = float(raw["w"]) if raw.get("w") else max(min_w, auto_w)
        self.h = float(raw["h"]) if raw.get("h") else max(
            {"unit": 44.0, "proc": 38.0, "doc": 44.0, "store": 38.0,
             "card": 40.0}[self.type], auto_h)
        self.x = self.y = 0.0


class Edge(object):
    __slots__ = ("src", "dst", "label", "from_side", "to_side", "route",
                 "via", "bend", "at0", "at1", "fs", "at0_fixed", "at1_fixed",
                 "s0", "s1", "style", "pts", "cs0", "cs1", "lane", "recip")

    def __init__(self, raw):
        if isinstance(raw, (list, tuple)):
            raw = {"from": raw[0], "to": raw[1]}
        self.src = str(raw.get("from") or raw.get("src") or raw.get("a") or "")
        self.dst = str(raw.get("to") or raw.get("dst") or raw.get("b") or "")
        if not self.src or not self.dst:
            raise ValueError("连线缺少 from/to: %r" % (raw,))
        self.label = raw.get("label") or raw.get("l") or ""
        self.from_side = SIDE_ALIASES.get(str(raw.get("fromSide", raw.get("fs", ""))).lower())
        self.to_side = SIDE_ALIASES.get(str(raw.get("toSide", raw.get("ts", ""))).lower())
        self.route = str(raw.get("route", "auto")).lower()
        self.via = raw.get("via") or []
        self.bend = raw.get("bend")
        self.at0_fixed = raw.get("at0") is not None
        self.at1_fixed = raw.get("at1") is not None
        self.at0 = float(raw.get("at0", 0.5))
        self.at1 = float(raw.get("at1", 0.5))
        self.fs = float(raw.get("fs") or 0)
        self.style = str(raw.get("style") or raw.get("arrow") or "line").lower()
        self.s0 = self.from_side
        self.s1 = self.to_side
        self.cs0 = None
        self.cs1 = None
        self.pts = None
        self.lane = 0.0
        self.recip = False


# ---------------------------------------------------------------- 布局

def auto_place(nodes, edges):
    """未指定 col/row 时，按拓扑层级自动分层。"""
    ids = {n.id: n for n in nodes}
    indeg = {n.id: 0 for n in nodes}
    out = {n.id: [] for n in nodes}
    for e in edges:
        if e.src in ids and e.dst in ids and e.src != e.dst:
            out[e.src].append(e.dst)
            indeg[e.dst] += 1
    depth = {n.id: 0 for n in nodes}
    queue = [n.id for n in nodes if indeg[n.id] == 0] or [nodes[0].id]
    seen = set(queue)
    order = []
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for nxt in out[cur]:
            depth[nxt] = max(depth[nxt], depth[cur] + 1)
            indeg[nxt] -= 1
            if indeg[nxt] <= 0 and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    for n in nodes:                      # 环上剩余节点兜底
        if n.id not in seen:
            depth[n.id] = max(depth.values()) + 1 if depth else 0
    for n in nodes:
        if n.col is None:
            n.col = depth[n.id]
        if n.row is None:
            n.row = 0
    # 同列内按出现顺序排布，避免全部叠在一行
    used = {}
    for n in nodes:
        k = int(n.col)
        used.setdefault(k, -1)
        if n.row is None or n.row == 0:
            used[k] += 1
            n.row = used[k]
    # 若人工给了 row 但与自动冲突，以人工为准：上面 already respects given row
    return nodes


def grid_layout(nodes, opts):
    cols = sorted({int(n.col) for n in nodes})
    rows = sorted({int(n.row) for n in nodes})
    col_w = {c: 0.0 for c in cols}
    row_h = {r: 0.0 for r in rows}
    for n in nodes:
        c, r = int(n.col), int(n.row)
        if n.span > 1:
            per = n.w / n.span
            for k in range(n.span):
                if c + k in col_w:
                    col_w[c + k] = max(col_w[c + k], per)
        else:
            col_w[c] = max(col_w[c], n.w)
        row_h[r] = max(row_h[r], n.h)
    hgap, vgap = opts["hgap"], opts["vgap"]
    col_x, x = {}, opts["margin_left"]
    for c in cols:
        col_x[c] = x
        x += col_w[c] + hgap
    row_y, y = {}, opts["margin_top"]
    for r in rows:
        row_y[r] = y
        y += row_h[r] + vgap
    for n in nodes:
        c, r = int(n.col), int(n.row)
        if n.span <= 1:
            total_w = col_w[c]
        else:
            total_w = sum(col_w[c + k] for k in range(n.span)
                          if c + k in col_w) + hgap * (n.span - 1)
        n.x = col_x[c] + (total_w - n.w) / 2.0
        n.y = row_y[r] + (row_h[r] - n.h) / 2.0
    right = max(n.x + n.w for n in nodes)
    bottom = max(n.y + n.h for n in nodes)
    return right, bottom


# ---------------------------------------------------------------- 图形

def anchor(n, side, at=0.5):
    at = min(0.98, max(0.02, at))
    if side == "top":
        p = (n.x + n.w * at, n.y)
    elif side == "bottom":
        p = (n.x + n.w * at, n.y + n.h)
    elif side == "left":
        p = (n.x, n.y + n.h * at)
    else:
        p = (n.x + n.w, n.y + n.h * at)
    if n.type == "unit":
        # 单位是圆/椭圆：把外接矩形上的点投影到椭圆边界，箭头才贴得住
        cx, cy = n.x + n.w / 2.0, n.y + n.h / 2.0
        rx, ry = n.w / 2.0, n.h / 2.0
        if rx > 0 and ry > 0:
            dx, dy = (p[0] - cx) / rx, (p[1] - cy) / ry
            d = (dx * dx + dy * dy) ** 0.5
            if d > 1e-6:
                p = (cx + (p[0] - cx) / d, cy + (p[1] - cy) / d)
    return p


def store_tab(n):
    """数据存储符号左端小格的宽度。"""
    return min(n.w * 0.15, n.h * 0.44, 20.0)


def shape_svg(n, pal, theme):
    x, y, w, h = n.x, n.y, n.w, n.h
    fill = pal["fill"][n.type]
    stroke = pal["stroke"]
    sw = 1.6 if theme == "textbook" else 1.5
    if n.type == "unit":
        d = (f'<ellipse cx="{num(x + w / 2)}" cy="{num(y + h / 2)}" '
             f'rx="{num(w / 2)}" ry="{num(h / 2)}" fill="{fill}" '
             f'stroke="{stroke}" stroke-width="{num(sw)}"/>')
    elif n.type == "doc":
        if DOC_STYLE == "wave":
            # 底边为 S 形：左半向下鼓出、右半向上凹陷（教材样本画法）
            amp = min(9.0, max(2.5, h * 0.20))     # 波形振幅
            k = amp * 1.33                          # 三次曲线的控制点偏移
            base = y + h
            d = (f'<path d="M{num(x)},{num(y)} H{num(x + w)} V{num(base)} '
                 f'C{num(x + w * 0.86)},{num(base - k)} '
                 f'{num(x + w * 0.64)},{num(base - k)} '
                 f'{num(x + w / 2)},{num(base)} '
                 f'C{num(x + w * 0.36)},{num(base + k)} '
                 f'{num(x + w * 0.14)},{num(base + k)} '
                 f'{num(x)},{num(base)} Z" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="{num(sw)}" '
                 f'stroke-linejoin="round"/>')
        else:
            # 标准画法：矩形 + 左下角折角（纸张/单证）
            c = min(w * 0.22, h * 0.30, 14.0)
            d = (f'<path d="M{num(x)},{num(y)} H{num(x + w)} V{num(y + h)} '
                 f'H{num(x + c)} L{num(x)},{num(y + h - c)} Z" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="{num(sw)}" '
                 f'stroke-linejoin="miter"/>'
                 f'<path d="M{num(x + c)},{num(y + h)} V{num(y + h - c)} '
                 f'H{num(x)}" fill="{pal["fold"]}" stroke="{stroke}" '
                 f'stroke-width="{num(sw * 0.9)}" stroke-linejoin="round"/>')
    elif n.type == "store":
        # 标准画法：左端带小格的开口方框（档案/文件）
        tab = store_tab(n)
        d = (f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}" '
             f'fill="{fill}" stroke="none"/>'
             f'<path d="M{num(x + w)},{num(y)} H{num(x)} V{num(y + h)} '
             f'H{num(x + w)}" fill="none" stroke="{stroke}" '
             f'stroke-width="{num(sw)}" stroke-linejoin="miter"/>'
             f'<path d="M{num(x + tab)},{num(y)} V{num(y + h)}" fill="none" '
             f'stroke="{stroke}" stroke-width="{num(sw * 0.9)}"/>')

    elif n.type == "card":
        c = min(14.0, w * 0.18, h * 0.3)
        d = (f'<path d="M{num(x + c)},{num(y)} H{num(x + w)} V{num(y + h)} '
             f'H{num(x)} V{num(y + c)} Z" fill="{fill}" stroke="{stroke}" '
             f'stroke-width="{num(sw)}" stroke-linejoin="round"/>')
    else:
        rx = 0.0 if theme == "textbook" else 3.0
        rxs = f' rx="{num(rx)}" ry="{num(rx)}"' if rx else ""
        d = (f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}"'
             f'{rxs} fill="{fill}" stroke="{stroke}" stroke-width="{num(sw)}"/>')
    return d


def text_svg(n, pal, line_height):
    fs = n.fs
    lh = fs * line_height
    if n.type == "store":
        tab = store_tab(n)
        cx = n.x + tab + (n.w - tab) / 2.0   # 文字避开左端小格
    else:
        cx = n.x + n.w / 2.0
    cy = n.y + n.h / 2.0
    first = cy - (len(n.lines) - 1) * lh / 2.0 + fs * 0.35
    out = [f'<text x="{num(cx)}" y="{num(first + i * lh)}" '
           f'font-size="{num(fs)}" fill="{pal["text"]}" '
           f'text-anchor="middle" font-family="{FONT_STACK}">'
           f'{esc(line)}</text>' for i, line in enumerate(n.lines)]
    return "".join(out)


def choose_sides(a, b):
    acx, acy = a.x + a.w / 2, a.y + a.h / 2
    bcx, bcy = b.x + b.w / 2, b.y + b.h / 2
    dx, dy = bcx - acx, bcy - acy
    if abs(dy) < max(a.h, b.h) * 0.45 and abs(dx) > 1:
        return ("right", "left") if dx > 0 else ("left", "right")
    if abs(dx) < max(a.w, b.w) * 0.35 and abs(dy) > 1:
        return ("bottom", "top") if dy > 0 else ("top", "bottom")
    if abs(dx) >= abs(dy):
        return ("right", "left") if dx > 0 else ("left", "right")
    return ("bottom", "top") if dy > 0 else ("top", "bottom")


def orth_path(p0, s0, p1, s1, stub, bend, route, lane=0.0):
    dv = {"top": (0, -1), "bottom": (0, 1), "left": (-1, 0), "right": (1, 0)}
    if route in ("straight", "s"):
        return [p0, p1]
    a = (p0[0] + dv[s0][0] * stub, p0[1] + dv[s0][1] * stub)
    b = (p1[0] + dv[s1][0] * stub, p1[1] + dv[s1][1] * stub)
    # 端点几乎对齐时吸附成一条直线，避免出现几像素的“台阶”
    # （但人工指定了 bend 就不要吸附，否则指定的拐点会被吃掉）
    if bend is None:
        if abs(a[0] - b[0]) < 6:
            b = (a[0], b[1])
        if abs(a[1] - b[1]) < 6:
            b = (b[0], a[1])
    pts = [p0, a]
    h0, h1 = dv[s0][0] != 0, dv[s1][0] != 0
    if bend is None and (abs(a[1] - b[1]) < 0.5 or abs(a[0] - b[0]) < 0.5):
        pts.append(b)
    elif route in ("h", "hv", "hvh"):
        midy = bend if bend is not None else (a[1] + b[1]) / 2.0
        midy += lane
        pts += [(a[0], midy), (b[0], midy)]
    elif route in ("v", "vh", "vhv"):
        midx = bend if bend is not None else (a[0] + b[0]) / 2.0
        midx += lane
        pts += [(midx, a[1]), (midx, b[1])]
    elif h0 and h1:
        if s0 == s1:                      # 同侧出线绕行
            if bend is not None:
                midx = bend + lane
            else:
                midx = ((max(a[0], b[0]) + 20) if dv[s0][0] > 0
                        else (min(a[0], b[0]) - 20)) + lane
        else:
            midx = (bend if bend is not None else (a[0] + b[0]) / 2.0) + lane
        pts += [(midx, a[1]), (midx, b[1])]
    elif (not h0) and (not h1):
        if s0 == s1:
            if bend is not None:
                midy = bend + lane
            else:
                midy = ((max(a[1], b[1]) + 20) if dv[s0][1] > 0
                        else (min(a[1], b[1]) - 20)) + lane
        else:
            midy = (bend if bend is not None else (a[1] + b[1]) / 2.0) + lane
        pts += [(a[0], midy), (b[0], midy)]
    elif h0 and not h1:
        pts.append((b[0], a[1]))
    else:
        pts.append((a[0], b[1]))
    pts.append(p1)
    clean = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - clean[-1][0]) > 0.4 or abs(p[1] - clean[-1][1]) > 0.4:
            clean.append(p)
    # 合并共线的中间点，输出更干净的路径
    out = [clean[0]]
    for i in range(1, len(clean) - 1):
        px, py = clean[i - 1]
        cx, cy = clean[i]
        nx, ny = clean[i + 1]
        if (abs(px - cx) < 0.4 and abs(cx - nx) < 0.4) or \
           (abs(py - cy) < 0.4 and abs(cy - ny) < 0.4):
            continue
        out.append(clean[i])
    out.append(clean[-1])
    return out


def trim_end(pts, amount):
    if len(pts) < 2:
        return pts
    (x0, y0), (x1, y1) = pts[-2], pts[-1]
    seg = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    if seg <= amount + 1:
        return pts
    k = (seg - amount) / seg
    pts = list(pts)
    pts[-1] = (x0 + (x1 - x0) * k, y0 + (y1 - y0) * k)
    return pts


def path_d(pts):
    d = f"M{num(pts[0][0])},{num(pts[0][1])}"
    for p in pts[1:]:
        d += f" L{num(p[0])},{num(p[1])}"
    return d


def longest_segment_mid(pts):
    best, bi = -1, 0
    for i in range(len(pts) - 1):
        l = abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1])
        if l > best:
            best, bi = l, i
    (x0, y0), (x1, y1) = pts[bi], pts[bi + 1]
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def _propagate(e, role, val, ids, horiz):
    """直连的两个端点同步偏移，否则会出现几像素的台阶。"""
    other_role = "at1" if role == "at0" else "at0"
    if getattr(e, other_role + "_fixed"):
        return
    a, b = ids[e.src], ids[e.dst]
    if horiz:
        straight = (abs((a.y + a.h / 2.0) - (b.y + b.h / 2.0)) < 26
                    and e.s0 in ("left", "right") and e.s1 in ("left", "right"))
    else:
        straight = (abs((a.x + a.w / 2.0) - (b.x + b.w / 2.0)) < 26
                    and e.s0 in ("top", "bottom") and e.s1 in ("top", "bottom"))
    if straight:
        setattr(e, other_role, val)


def resolve_sides(edges, ids):
    """确定每条连线的出入边，并安排箭头在边上的落点。"""
    for e in edges:
        a, b = ids[e.src], ids[e.dst]
        auto0, auto1 = choose_sides(a, b)
        e.s0 = e.from_side or auto0
        e.s1 = e.to_side or auto1

    def spread(key, at_field, fixed_field):
        groups = {}
        for e in edges:
            groups.setdefault(key(e), []).append(e)
        for _, lst in groups.items():
            if len(lst) < 2:
                continue
            side = lst[0].s1 if at_field == "at1" else lst[0].s0
            horizontal = side in ("left", "right")

            def sort_key(e):
                ref = ids[e.src] if at_field == "at1" else ids[e.dst]
                return ((ref.x + ref.w / 2.0, ref.y + ref.h / 2.0) if horizontal
                        else (ref.y + ref.h / 2.0, ref.x + ref.w / 2.0))

            lst.sort(key=sort_key)
            # 块状箭头比细线粗得多，按"权重"分配落点，让它多占一点位置
            weights = [2.2 if e.style in ("block", "flow") else 1.0 for e in lst]
            total = sum(weights)
            acc = 0.0
            for e, w in zip(lst, weights):
                acc += w
                if not getattr(e, fixed_field):
                    setattr(e, at_field, round(acc / total - w / total / 2.0, 3))

    spread(lambda e: (e.dst, e.s1), "at1", "at1_fixed")
    spread(lambda e: (e.src, e.s0), "at0", "at0_fixed")

    # 同一侧挂了多条线时，落点必须保留错开的结果，不能再拉回中点
    busy = {}
    for e in edges:
        busy[(e.src, e.s0)] = busy.get((e.src, e.s0), 0) + 1
        busy[(e.dst, e.s1)] = busy.get((e.dst, e.s1), 0) + 1

    # 1) 正上/正下、正左/正右的连线：两端对齐，走成一条直线
    for e in edges:
        if busy.get((e.src, e.s0), 0) > 1 or busy.get((e.dst, e.s1), 0) > 1:
            continue
        a, b = ids[e.src], ids[e.dst]
        dx = abs((a.x + a.w / 2.0) - (b.x + b.w / 2.0))
        dy = abs((a.y + a.h / 2.0) - (b.y + b.h / 2.0))
        vertical = e.s0 in ("top", "bottom") and e.s1 in ("top", "bottom")
        horizontal = e.s0 in ("left", "right") and e.s1 in ("left", "right")
        if vertical and dx < 26:
            v = e.at0 if e.at0_fixed else e.at1
            if not e.at0_fixed:
                e.at0 = v
            if not e.at1_fixed:
                e.at1 = v
        elif horizontal and dy < 26:
            v = e.at0 if e.at0_fixed else e.at1
            if not e.at0_fixed:
                e.at0 = v
            if not e.at1_fixed:
                e.at1 = v

    # 2) 读写成对（更新库存文件 ⇅ 库存文件）：错开成两条平行线
    pairs = {(e.src, e.dst) for e in edges}
    seen = set()
    for e in edges:
        key = tuple(sorted((e.src, e.dst)))
        if key in seen or (e.dst, e.src) not in pairs:
            continue
        seen.add(key)
        rev = next(x for x in edges if x.src == e.dst and x.dst == e.src)
        e.recip = rev.recip = True
        for ed, val in ((e, 0.35), (rev, 0.65)):
            if not ed.at0_fixed:
                ed.at0 = val
            if not ed.at1_fixed:
                ed.at1 = val

    # 3) 同一节点同一侧既有进又有出：落点错开，避免在框边上打结
    mixed = {}
    for e in edges:
        mixed.setdefault((e.src, e.s0), []).append((e, "at0"))
        mixed.setdefault((e.dst, e.s1), []).append((e, "at1"))
    for (nid, side), lst in mixed.items():
        if len({r for _, r in lst}) < 2:
            continue
        node = ids[nid]
        horiz = side in ("left", "right")
        infos = []
        for e, role in lst:
            other = ids[e.dst] if role == "at0" else ids[e.src]
            if horiz:
                gap = abs((other.y + other.h / 2.0) - (node.y + node.h / 2.0))
                delta = (other.x + other.w / 2.0) - (node.x + node.w / 2.0)
            else:
                gap = abs((other.x + other.w / 2.0) - (node.x + node.w / 2.0))
                delta = (other.x + other.w / 2.0) - (node.x + node.w / 2.0)
            infos.append((e, role, gap, delta))
        infos.sort(key=lambda t: (t[2], t[3]))
        n = len(infos)
        aligned = [i for i, t in enumerate(infos) if t[2] < 26]
        if n == 2 and len(aligned) == 1:
            for i, (e, role, _gap, delta) in enumerate(infos):
                if getattr(e, role + "_fixed"):
                    continue
                if i == aligned[0]:
                    val = 0.5
                else:
                    val = round(min(0.85, max(0.15,
                                0.5 + (0.28 if delta > 0 else -0.28))), 3)
                setattr(e, role, val)
                _propagate(e, role, val, ids, horiz)
        else:
            for i, (e, role, _gap, _delta) in enumerate(infos):
                if getattr(e, role + "_fixed"):
                    continue
                val = round((i + 1.0) / (n + 1.0), 3)
                setattr(e, role, val)
                _propagate(e, role, val, ids, horiz)


def inside(pt, n, margin=2.0):
    return (n.x + margin < pt[0] < n.x + n.w - margin and
            n.y + margin < pt[1] < n.y + n.h - margin)


def crosses_box(pts, a, b):
    """折线的中间拐点落进形状内部 → 说明会从框里穿过。"""
    return any(inside(p, a) or inside(p, b) for p in pts[1:-1])


def edge_svg(e, nodes, pal, opts):
    a, b = nodes[e.src], nodes[e.dst]
    p0 = anchor(a, e.cs0 or e.s0, e.at0)
    p1 = anchor(b, e.cs1 or e.s1, e.at1)
    if e.style in ("block", "flow"):
        # 业务流向：沿已算好的正交折线画空心块状箭头（可拐弯）
        return block_arrow_poly(e.pts if e.pts else [p0, p1], pal,
                                opts["edge_width"] + 0.4)
    pts = e.pts if e.pts else orth_path(p0, e.cs0 or e.s0, p1, e.cs1 or e.s1,
                                        opts["stub"], e.bend, e.route)
    pts = trim_end(pts, opts["arrow_len"])
    out = [f'<path d="{path_d(pts)}" fill="none" stroke="{pal["edge"]}" '
           f'stroke-width="{num(opts["edge_width"])}" stroke-linecap="round" '
           f'stroke-linejoin="round" marker-end="url(#tfd-arrow)"/>']
    if e.label and opts.get("edge_labels"):
        lx, ly = longest_segment_mid(pts)
        fs = e.fs or opts["edge_fs"]
        tw = text_width(e.label, fs) + 8
        out.append(f'<rect x="{num(lx - tw / 2)}" y="{num(ly - fs * 0.72)}" '
                   f'width="{num(tw)}" height="{num(fs * 1.4)}" fill="{pal["bg"]}"/>')
        out.append(f'<text x="{num(lx)}" y="{num(ly + fs * 0.35)}" '
                   f'font-size="{num(fs)}" fill="{pal["text"]}" '
                   f'text-anchor="middle" font-family="{FONT_STACK}">'
                   f'{esc(e.label)}</text>')
    return "".join(out)


# ---------------------------------------------------------------- 图例

WARNINGS = []

# 与样本图一致的 5 类符号；业务流向（块状箭头）用 legend_flow 打开
LEGEND_ITEMS = [
    ("unit", "业务处理单位或部门"),
    ("flow", "数据流动及方向"),
    ("doc", "各类单证、报表"),
    ("store", "数据存储或文档"),
    ("proc", "业务处理功能"),
]
LEGEND_EXTRA = [("block", "业务流向")]


def block_arrow_poly(pts, pal, sw=1.6, shaft=14.0, head_w=30.0, head_l=18.0):
    """沿正交折线画空心块状箭头（业务流向符号），支持拐一次弯。"""
    clean = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - clean[-1][0]) > 0.5 or abs(p[1] - clean[-1][1]) > 0.5:
            clean.append(p)
    if len(clean) < 2:
        return ""
    hs, hh = shaft / 2.0, head_w / 2.0
    dx = clean[-1][0] - clean[-2][0]
    dy = clean[-1][1] - clean[-2][1]
    length = max(1e-6, (dx * dx + dy * dy) ** 0.5)
    ux, uy = dx / length, dy / length
    hl = min(head_l, max(6.0, length * 0.55))
    tip = clean[-1]
    base = (tip[0] - ux * hl, tip[1] - uy * hl)
    path = clean[:-1] + [base]

    ns = []
    for i in range(len(path) - 1):
        ax = path[i + 1][0] - path[i][0]
        ay = path[i + 1][1] - path[i][1]
        l = max(1e-6, (ax * ax + ay * ay) ** 0.5)
        ns.append((-ay / l, ax / l))          # 左法向

    def side(sign):
        out = []
        for i, p in enumerate(path):
            if i == 0:
                out.append((p[0] + ns[0][0] * hs * sign, p[1] + ns[0][1] * hs * sign))
            elif i == len(path) - 1:
                out.append((p[0] + ns[-1][0] * hs * sign, p[1] + ns[-1][1] * hs * sign))
            else:
                out.append((p[0] + (ns[i - 1][0] + ns[i][0]) * hs * sign,
                            p[1] + (ns[i - 1][1] + ns[i][1]) * hs * sign))
        return out

    left, right = side(1), side(-1)
    ne = ns[-1]
    poly = (left
            + [(base[0] + ne[0] * hh, base[1] + ne[1] * hh), tip,
               (base[0] - ne[0] * hh, base[1] - ne[1] * hh)]
            + right[::-1])
    d = "M" + " L".join(f"{num(x)},{num(y)}" for x, y in poly) + " Z"
    return (f'<path d="{d}" fill="{pal["bg"]}" stroke="{pal["edge"]}" '
            f'stroke-width="{num(sw)}" stroke-linejoin="miter"/>')


def block_arrow_svg(p0, p1, pal, sw=1.6, shaft=12.0, head_w=24.0, head_l=16.0):
    """空心块状箭头：业务流向符号（只画水平或垂直方向）。"""
    x0, y0 = p0
    x1, y1 = p1
    if abs(x1 - x0) >= abs(y1 - y0):
        sign = 1.0 if x1 >= x0 else -1.0
        length = abs(x1 - x0)
        hl = min(head_l, max(7.0, length * 0.45))
        sx = x1 - sign * hl
        pts = [(x0, y0 - shaft / 2), (sx, y0 - shaft / 2), (sx, y0 - head_w / 2),
               (x1, y0), (sx, y0 + head_w / 2), (sx, y0 + shaft / 2),
               (x0, y0 + shaft / 2)]
    else:
        sign = 1.0 if y1 >= y0 else -1.0
        length = abs(y1 - y0)
        hl = min(head_l, max(7.0, length * 0.45))
        sy = y1 - sign * hl
        pts = [(x0 - shaft / 2, y0), (x0 - shaft / 2, sy), (x0 - head_w / 2, sy),
               (x0, y1), (x0 + head_w / 2, sy), (x0 + shaft / 2, sy),
               (x0 + shaft / 2, y0)]
    d = "M" + " L".join(f"{num(a)},{num(b)}" for a, b in pts) + " Z"
    return (f'<path d="{d}" fill="{pal["bg"]}" stroke="{pal["edge"]}" '
            f'stroke-width="{num(sw)}" stroke-linejoin="miter"/>')


class MiniNode(object):
    def __init__(self, x, y, w, h, t):
        self.x, self.y, self.w, self.h, self.type = x, y, w, h, t
        self.lines, self.fs = [], 0


SW, SH, TEXTGAP, ITEMGAP = 46.0, 24.0, 10.0, 34.0


def legend_plan(opts, max_w):
    """把图例排成若干行，返回 (行内容, 最大行宽, 总高)。"""
    fs = opts["fs"] * 0.82
    base = LEGEND_ITEMS + (LEGEND_EXTRA if opts.get("legend_flow") else [])
    items = [(k, n, SW + TEXTGAP + text_width(n, fs) + ITEMGAP)
             for k, n in base]
    rows, cur, cur_w = [], [], 0.0
    for it in items:
        if cur and cur_w + it[2] > max_w:
            rows.append((cur, cur_w))
            cur, cur_w = [], 0.0
        cur.append(it)
        cur_w += it[2]
    if cur:
        rows.append((cur, cur_w))
    total = len(rows) * SH + (len(rows) - 1) * 12.0
    return rows, max((r[1] for r in rows), default=0.0) - ITEMGAP, total, fs


def legend_svg(x0, y0, pal, opts, theme, rows, fs):
    parts = []
    for ri, (row, _) in enumerate(rows):
        cx, ry = x0, y0 + ri * (SH + 12.0)
        for kind, name, iw in row:
            if kind in ("flow", "arrow"):
                parts.append(
                    f'<path d="M{num(cx)},{num(ry + SH / 2)} L{num(cx + SW - 6)},{num(ry + SH / 2)}" '
                    f'fill="none" stroke="{pal["edge"]}" stroke-width="1.4" '
                    f'marker-end="url(#tfd-arrow)"/>')
            elif kind == "block":
                parts.append(block_arrow_svg((cx, ry + SH / 2), (cx + SW, ry + SH / 2),
                                             pal, 1.4, shaft=9.0, head_w=18.0,
                                             head_l=13.0))
            else:
                if kind == "unit" and \
                        str(opts.get("unit_shape", "auto")).lower() != "ellipse":
                    # 单位符号在图上画正圆，图例也画正圆
                    mini = MiniNode(cx + (SW - SH) / 2, ry, SH, SH, kind)
                else:
                    mini = MiniNode(cx, ry, SW, SH, kind)
                parts.append(shape_svg(mini, pal, theme).replace(
                    'stroke-width="1.5"', 'stroke-width="1.4"').replace(
                    'stroke-width="1.6"', 'stroke-width="1.4"'))
            parts.append(f'<text x="{num(cx + SW + TEXTGAP)}" '
                         f'y="{num(ry + SH / 2 + fs * 0.35)}" '
                         f'font-size="{num(fs)}" fill="{pal["muted"]}" '
                         f'font-family="{FONT_STACK}">{esc(name)}</text>')
            cx += iw
    return "".join(parts)


# ---------------------------------------------------------------- 主流程

def check_overlaps(nodes):
    """形状互相压住时给出警告，方便改 col/row 后重跑。"""
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            a, b = nodes[i], nodes[j]
            ox = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
            oy = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
            if ox > 2 and oy > 2:
                WARNINGS.append("节点重叠: %s 与 %s（建议改用不同的 col/row）"
                                % (a.id, b.id))


SIDES = ("top", "bottom", "left", "right")


def _ori(a, b, c):
    v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if abs(v) < 1e-9:
        return 0
    return 1 if v > 0 else -1


def _seg_cross(p1, p2, p3, p4):
    o1, o2 = _ori(p1, p2, p3), _ori(p1, p2, p4)
    o3, o4 = _ori(p3, p4, p1), _ori(p3, p4, p2)
    return 0 not in (o1, o2, o3, o4) and o1 != o2 and o3 != o4


def _cross_point(p1, p2, p3, p4):
    """两条正交线段的交点；平行或不相交返回 None。

    注意：水平段的取值范围在 x 上、垂直段的在 y 上，两组界限不能混用。
    """
    h1 = abs(p1[1] - p2[1]) < 0.5
    h3 = abs(p3[1] - p4[1]) < 0.5
    if h1 == h3:
        return None
    if h1:
        y, x = p1[1], p3[0]
        xa, xb = sorted((p1[0], p2[0]))
        ya, yb = sorted((p3[1], p4[1]))
    else:
        y, x = p3[1], p1[0]
        xa, xb = sorted((p3[0], p4[0]))
        ya, yb = sorted((p1[1], p2[1]))
    if xa < x < xb and ya < y < yb:
        return (x, y)
    return None


def near_node(pt, n, pad=6.0):
    return (n.x - pad <= pt[0] <= n.x + n.w + pad and
            n.y - pad <= pt[1] <= n.y + n.h + pad)


def seg_hits_node(p, q, n, margin=-2.5):
    """水平/垂直线段是否穿过某个形状的内部。"""
    x0, y0 = n.x + margin, n.y + margin
    x1, y1 = n.x + n.w - margin, n.y + n.h - margin
    if x1 <= x0 or y1 <= y0:
        return False
    if abs(p[1] - q[1]) < 0.5:
        if not (y0 < p[1] < y1):
            return False
        lo, hi = sorted((p[0], q[0]))
        return hi > x0 and lo < x1
    if abs(p[0] - q[0]) < 0.5:
        if not (x0 < p[0] < x1):
            return False
        lo, hi = sorted((p[1], q[1]))
        return hi > y0 and lo < y1
    return False


def _seg_overlap(p1, p2, p3, p4, tol=2.0):
    """两段共线且投影重叠 → 视觉上会粘成一条线（比交叉更歧义）。"""
    if abs(p1[1] - p2[1]) < 0.5 and abs(p3[1] - p4[1]) < 0.5 \
            and abs(p1[1] - p3[1]) < 0.5:
        lo1, hi1 = sorted((p1[0], p2[0]))
        lo2, hi2 = sorted((p3[0], p4[0]))
        return min(hi1, hi2) - max(lo1, lo2) > tol
    if abs(p1[0] - p2[0]) < 0.5 and abs(p3[0] - p4[0]) < 0.5 \
            and abs(p1[0] - p3[0]) < 0.5:
        lo1, hi1 = sorted((p1[1], p2[1]))
        lo2, hi2 = sorted((p3[1], p4[1]))
        return min(hi1, hi2) - max(lo1, lo2) > tol
    return False


def seg_hits_core(p, q, n):
    """是否穿过形状的"核心区"（不是贴着边走，而是从中间穿过去）。"""
    mx, my = min(n.w * 0.26, 20.0), min(n.h * 0.26, 14.0)
    x0, y0 = n.x + mx, n.y + my
    x1, y1 = n.x + n.w - mx, n.y + n.h - my
    if x1 <= x0 or y1 <= y0:
        return False
    if abs(p[1] - q[1]) < 0.5:
        if not (y0 < p[1] < y1):
            return False
        lo, hi = sorted((p[0], q[0]))
        return hi > x0 and lo < x1
    if abs(p[0] - q[0]) < 0.5:
        if not (x0 < p[0] < x1):
            return False
        lo, hi = sorted((p[1], q[1]))
        return hi > y0 and lo < y1
    return False


def side_ok(prev, side, n, tol=4.0):
    """收线点是否真的落在指定那一侧的外面。"""
    if side == "left":
        return prev[0] <= n.x + tol
    if side == "right":
        return prev[0] >= n.x + n.w - tol
    if side == "top":
        return prev[1] <= n.y + tol
    return prev[1] >= n.y + n.h - tol


def approach_ok(pts, s0, s1, a, b):
    return (len(pts) >= 3 and side_ok(pts[1], s0, a)
            and side_ok(pts[-2], s1, b))


def path_len(pts):
    return sum(abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1])
               for i in range(len(pts) - 1))


DIRV = {"top": (0, -1), "bottom": (0, 1), "left": (-1, 0), "right": (1, 0)}


def _candidates(a, b, s0, s1):
    """只考虑"朝向目标"的出入边，避免出现从下方绕回来的 U 形走线。"""
    dx = (b.x + b.w / 2.0) - (a.x + a.w / 2.0)
    dy = (b.y + b.h / 2.0) - (a.y + a.h / 2.0)
    norm = max(1e-6, (dx * dx + dy * dy) ** 0.5)
    def ok(side, sign):
        vx, vy = DIRV[side]
        return (vx * dx * sign + vy * dy * sign) / norm > -0.15
    from_ok = [s for s in SIDES if ok(s, 1)]
    to_ok = [s for s in SIDES if ok(s, -1)]
    # 同侧进、同侧出 = 绕一个 U 形回到同一个点，TFD 里读不通，直接不要
    out = [(p, q) for p in from_ok for q in to_ok if p != q]
    # 理论上总有解；万一没有就退回全部组合
    return out or [(p, q) for p in SIDES for q in SIDES]


def _path_cost(pts, s0, s1, a, b, nodes, geo, e, base, side_pen):
    cost = 8.0 * max(0, len(pts) - 2) + 0.03 * path_len(pts) + side_pen
    if not approach_ok(pts, s0, s1, a, b):
        cost += 800.0                      # 名义从左侧进、实际从右边绕过来
    for n in nodes:
        if n is a or n is b:
            continue
        if any(seg_hits_node(pts[i], pts[i + 1], n) for i in range(len(pts) - 1)):
            cost += 1000.0
    for n in (a, b):
        if any(seg_hits_core(pts[i], pts[i + 1], n) for i in range(len(pts) - 1)):
            cost += 900.0                  # 从自己这一头/那一头的中间穿过去
    dx = (b.x + b.w / 2.0) - (a.x + a.w / 2.0)
    dy = (b.y + b.h / 2.0) - (a.y + a.h / 2.0)
    norm = max(1e-6, (dx * dx + dy * dy) ** 0.5)
    for side, sign in ((s0, 1), (s1, -1)):
        vx, vy = DIRV[side]
        if (vx * dx * sign + vy * dy * sign) / norm < 0:
            cost += 260.0                  # 朝反方向出线，几何上合法但读着别扭
    for f, fpts in geo.items():
        if f is e:
            continue
        shared_node = None
        if e.src == f.src or e.src == f.dst:
            shared_node = ids_ref[e.src]
        elif e.dst == f.src or e.dst == f.dst:
            shared_node = ids_ref[e.dst]
        cross = overlap = False
        for i in range(len(pts) - 1):
            for j in range(len(fpts) - 1):
                pt = _cross_point(pts[i], pts[i + 1], fpts[j], fpts[j + 1])
                if pt is not None:
                    if shared_node is not None and near_node(pt, shared_node):
                        continue               # 就在公共节点身上，正常握手
                    cross = True
                elif _seg_overlap(pts[i], pts[i + 1], fpts[j], fpts[j + 1]):
                    overlap = True
        if cross:
            cost += 500.0
        if overlap:
            cost += 420.0
    if (s0, s1) != base:
        cost += 70.0 if (e.from_side or e.to_side) else 16.0
    return cost


ids_ref = {}


def edge_conflicts(e, geo):
    """统计一条线当前的冲突数，用于定向修复。"""
    n = 0
    for f, fpts in geo.items():
        if f is e or not e.pts:
            continue
        shared_node = None
        if e.src == f.src or e.src == f.dst:
            shared_node = ids_ref.get(e.src)
        elif e.dst == f.src or e.dst == f.dst:
            shared_node = ids_ref.get(e.dst)
        for i in range(len(e.pts) - 1):
            for j in range(len(fpts) - 1):
                pt = _cross_point(e.pts[i], e.pts[i + 1], fpts[j], fpts[j + 1])
                if pt is not None:
                    if shared_node is not None and near_node(pt, shared_node):
                        continue
                    n += 1
                elif _seg_overlap(e.pts[i], e.pts[i + 1], fpts[j], fpts[j + 1]):
                    n += 1
    return n


def route_edges(edges, ids, nodes, opts):
    """为每条连线选出入边组合：先整体择优，再对残余冲突做定向修复。"""
    global ids_ref
    ids_ref = ids
    stub = opts["stub"]
    geo = {}
    side_roles = {}
    for e in edges:
        side_roles.setdefault((e.src, e.s0), set()).add("out")
        side_roles.setdefault((e.dst, e.s1), set()).add("in")

    def best_for(e, geo):
        a, b = ids[e.src], ids[e.dst]
        base = (e.s0, e.s1)
        best = None
        for s0, s1 in _candidates(a, b, e.s0, e.s1):
            side_pen = 0.0
            if b.type == "store" and s1 == "right":
                side_pen += 14.0
            if a.type == "store" and s0 == "right":
                side_pen += 14.0
            for lane in (0.0, -22.0, 22.0, -44.0, 44.0, -66.0, 66.0):
                pts = orth_path(anchor(a, s0, e.at0), s0,
                                anchor(b, s1, e.at1), s1, stub, e.bend,
                                "auto", lane)
                cost = _path_cost(pts, s0, s1, a, b, nodes, geo, e, base,
                                  side_pen) + abs(lane) * 0.25
                # 同一侧既有一进又有一出，容易在框边上打结
                if not e.recip:      # 读写成对本来就该共用这两条边
                    if "in" in side_roles.get((e.src, s0), ()):
                        cost += 300.0
                    if "out" in side_roles.get((e.dst, s1), ()):
                        cost += 300.0
                if best is None or cost < best[0] - 0.01:
                    best = (cost, s0, s1, pts, lane)
        return best

    fixed = []
    for e in edges:
        a, b = ids[e.src], ids[e.dst]
        if e.style in ("block", "flow") and e.from_side and e.to_side:
            e.pts = orth_path(anchor(a, e.from_side, e.at0), e.from_side,
                              anchor(b, e.to_side, e.at1), e.to_side,
                              stub, e.bend, "auto")
            e.cs0, e.cs1 = e.from_side, e.to_side
            geo[e] = e.pts
            continue
        if e.route not in ("auto",) or (e.via and e.route == "poly"):
            p0 = anchor(a, e.s0, e.at0)
            p1 = anchor(b, e.s1, e.at1)
            if e.route == "poly" and e.via:
                e.pts = [p0] + [(float(v[0]), float(v[1])) for v in e.via] + [p1]
            else:
                e.pts = orth_path(p0, e.s0, p1, e.s1, stub, e.bend, e.route)
            e.cs0, e.cs1 = e.s0, e.s1
            geo[e] = e.pts
            continue
        fixed.append(e)

    for _round in range(3):
        for e in fixed:
            best = best_for(e, geo)
            if best:
                _, s0, s1, pts, lane = best
                e.cs0, e.cs1, e.pts, e.lane = s0, s1, pts, lane
                geo[e] = pts

    # 修复：只重排仍有冲突的边，直到清零或轮次用尽
    for _round in range(8):
        bad = [e for e in fixed if edge_conflicts(e, geo) > 0]
        if not bad:
            break
        bad.sort(key=lambda x: -edge_conflicts(x, geo))
        for e in bad:
            best = best_for(e, geo)
            if best:
                cost, s0, s1, pts, lane = best
                e.cs0, e.cs1, e.pts, e.lane = s0, s1, pts, lane
                geo[e] = pts
    return edges



def boost_for_congestion(nodes, edges, ids, opts):
    """同一侧挂了多条线时，自动把节点放大、把间距加宽，避免箭头挤在一起。"""
    load = {}
    for e in edges:
        a, b = ids[e.src], ids[e.dst]
        auto0, auto1 = choose_sides(a, b)
        s0 = e.from_side or auto0
        s1 = e.to_side or auto1
        load[(e.src, s0)] = load.get((e.src, s0), 0) + 1
        load[(e.dst, s1)] = load.get((e.dst, s1), 0) + 1

    crowded_v = any(c >= 2 for (nid, side), c in load.items()
                    if side in ("left", "right"))
    crowded_h = any(c >= 2 for (nid, side), c in load.items()
                    if side in ("top", "bottom"))
    has_block = any(e.style in ("block", "flow") for e in edges)

    for n in nodes:
        busy = [c for (nid, _side), c in load.items() if nid == n.id and c >= 2]
        if not busy or n.explicit:
            continue
        n.h = max(n.h, 48.0)          # 上下两个锚点才拉得开
        n.w = max(n.w, 92.0)

    if crowded_v or has_block:
        opts["hgap"] = max(opts["hgap"], 110.0 if has_block else 64.0)
    if crowded_h:
        opts["vgap"] = max(opts["vgap"], 46.0)


def build(spec, opts_override=None):
    opts = {
        "fs": 13.0,
        "edge_fs": 11.0,
        "pad": 8.0,
        "max_node_w": 132.0,
        "line_height": 1.28,
        "hgap": 34.0,
        "vgap": 52.0,
        "margin": 26.0,
        "margin_left": 26.0,
        "margin_top": 26.0,
        "stub": 18.0,
        "arrow_len": 9.0,
        "edge_width": 1.4,
        "theme": "textbook",
        "doc_style": "wave",
        "unit_shape": "auto",
        "legend_flow": False,
        "edge_labels": False,
        "legend": False,
        "show_title": False,
        "title": "",
        "subtitle": "",
        "note": "",
        "direction": "LR",
    }
    opts.update({k: v for k, v in spec.get("options", {}).items() if v is not None})
    for k in ("fs", "edge_fs", "pad", "max_node_w", "line_height", "hgap",
              "vgap", "stub", "arrow_len", "edge_width", "doc_style", "theme",
              "legend", "legend_flow", "unit_shape", "direction", "edge_labels",
              "show_title"):
        if k in spec and spec[k] is not None:
            opts[k] = spec[k]
    if opts_override:
        opts.update({k: v for k, v in opts_override.items() if v is not None})
    theme = opts["theme"] if opts["theme"] in PALETTE else "textbook"
    pal = PALETTE[theme]
    global DOC_STYLE
    DOC_STYLE = "fold" if str(opts.get("doc_style", "wave")).lower() == "fold" else "wave"

    # 图里不放标题：标题由调用方在回复正文里写，图只负责图形本身
    show_title = bool(opts.get("show_title"))
    title = (opts["title"] or spec.get("title") or "") if show_title else ""
    subtitle = (opts["subtitle"] or spec.get("subtitle") or "") if show_title else ""
    note = (spec.get("note") or opts["note"]) if show_title else ""

    raw_nodes = spec.get("nodes") or []
    if not raw_nodes:
        raise ValueError("spec.nodes 为空")
    nodes = [Node(r, opts) for r in raw_nodes]
    ids = {}
    for n in nodes:
        if n.id in ids:
            raise ValueError("节点 id 重复: %s" % n.id)
        ids[n.id] = n
    edges = [Edge(r) for r in (spec.get("edges") or [])]
    for e in edges:
        if e.src not in ids:
            raise ValueError("连线引用了不存在的节点: %s" % e.src)
        if e.dst not in ids:
            raise ValueError("连线引用了不存在的节点: %s" % e.dst)

    boost_for_congestion(nodes, edges, ids, opts)

    if any(n.col is None or n.row is None for n in nodes):
        auto_place(nodes, edges, opts.get("direction", "LR"))

    head = opts["margin_top"]
    ts = opts["fs"] * 1.45
    if title:
        head += ts * 1.25
    if subtitle:
        head += opts["fs"] * 1.5
        head += opts["fs"] * 1.05
    opts["margin_top"] = head

    right, bottom = grid_layout(nodes, opts)
    check_overlaps(nodes)
    resolve_sides(edges, ids)
    route_edges(edges, ids, nodes, opts)

    # 画布包围盒要用"节点 + 所有连线拐点"，否则绕行的线会被裁掉
    xs, ys = [], []
    for n in nodes:
        xs += [n.x, n.x + n.w]
        ys += [n.y, n.y + n.h]
    for e in edges:
        for pt in (e.pts or []):
            xs.append(pt[0])
            ys.append(pt[1])
    minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
    ddx = max(0.0, opts["margin"] - minx)
    ddy = max(0.0, opts["margin_top"] - miny)
    if ddx or ddy:
        for n in nodes:
            n.x += ddx
            n.y += ddy
        for e in edges:
            if e.pts:
                e.pts = [(pt[0] + ddx, pt[1] + ddy) for pt in e.pts]
    right = maxx + ddx
    bottom = maxy + ddy

    if any(e.style in ("block", "flow") for e in edges):
        opts["legend_flow"] = True      # 图上用了块状箭头，图例必须能解释它
    rows, legend_w, legend_h, legend_fs = [], 0.0, 0.0, opts["fs"] * 0.82
    if opts["legend"]:
        # 图例宽度不超过图形本身，超了就折成多行
        avail = max(right - opts["margin"], 420.0)
        rows, legend_w, legend_h, legend_fs = legend_plan(opts, avail)

    width = max(right + opts["margin"], legend_w + 2 * opts["margin"], 360)
    height = bottom + opts["margin"]
    if opts["legend"]:
        height += 44 + legend_h
    if note:
        height += opts["fs"] * 1.9

    svg = []
    svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{num(width)}" '
               f'height="{num(height)}" viewBox="0 0 {num(width)} {num(height)}" '
               f'font-family="{FONT_STACK}">')
    svg.append(f'<rect width="100%" height="100%" fill="{pal["bg"]}"/>')
    svg.append('<defs><marker id="tfd-arrow" viewBox="0 0 10 8" refX="9.2" refY="4" '
               'markerWidth="10" markerHeight="8" markerUnits="userSpaceOnUse" '
               'orient="auto">'
               f'<path d="M0,0 L10,4 L0,8 Z" fill="{pal["edge"]}"/></marker></defs>')

    if title:
        svg.append(f'<text x="{num(opts["margin"])}" y="{num(opts["margin"] + ts * 0.78)}" '
                   f'font-size="{num(ts)}" font-weight="600" fill="{pal["text"]}">'
                   f'{esc(title)}</text>')
    if subtitle:
        sy = opts["margin"] + ts * 1.25 + opts["fs"] * 1.05
        svg.append(f'<text x="{num(opts["margin"])}" y="{num(sy)}" '
                   f'font-size="{num(opts["fs"] * 0.95)}" fill="{pal["muted"]}">'
                   f'{esc(subtitle)}</text>')

    for e in edges:
        svg.append(edge_svg(e, ids, pal, opts))
    for n in nodes:
        svg.append(shape_svg(n, pal, theme))
        svg.append(text_svg(n, pal, opts["line_height"]))

    if opts["legend"]:
        ly = bottom + 20
        svg.append(legend_svg(opts["margin"], ly, pal, opts, theme,
                              rows, legend_fs))
    if note:
        ny = height - opts["fs"] * 0.9
        svg.append(f'<text x="{num(opts["margin"])}" y="{num(ny)}" '
                   f'font-size="{num(opts["fs"] * 0.85)}" fill="{pal["muted"]}">'
                   f'{esc(note)}</text>')

    svg.append("</svg>")
    return "\n".join(svg), (width, height), nodes, edges


def main(argv=None):
    ap = argparse.ArgumentParser(description="TFD 业务流程图 SVG 生成器")
    ap.add_argument("spec", help="规格文件（JSON，UTF-8）")
    ap.add_argument("-o", "--out", help="输出 SVG 路径；缺省与输入同名的 .svg")
    ap.add_argument("--theme", help="textbook（默认，教材黑白）| soft（浅色填充）")
    ap.add_argument("--no-legend", action="store_true", help="不画图例")
    ap.add_argument("--title", help="覆盖标题")
    args = ap.parse_args(argv)

    with open(args.spec, "r", encoding="utf-8") as f:
        spec = json.load(f)

    override = {"theme": args.theme, "title": args.title}
    if args.title:
        override["show_title"] = True
    if args.no_legend:
        override["legend"] = False

    try:
        svg, (w, h), nodes, edges = build(spec, override)
    except Exception as exc:                                  # noqa: BLE001
        sys.stderr.write("[tfd] 规格有误: %s\n" % exc)
        return 2

    out = args.out or os.path.splitext(args.spec)[0] + ".svg"
    with open(out, "w", encoding="utf-8") as f:
        f.write(svg)
    print("[tfd] 已生成 %s  (%dx%d, %d 个节点, %d 条连线)"
          % (out, round(w), round(h), len(nodes), len(edges)))
    for wmsg in WARNINGS:
        sys.stderr.write("[tfd] 提示: %s\n" % wmsg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
