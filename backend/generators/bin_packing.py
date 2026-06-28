"""Карта раскроя: упаковка деталей на стандартные листы (MaxRects, FFD + поворот 90°).

Лист: 2440×1220 мм. Отступ от края 10 мм, пропил между деталями 4 мм.
Результат: список листов с размещениями, % использования, SVG-превью.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

SHEET_W = 2440.0
SHEET_H = 1220.0
MARGIN = 10.0    # отступ от края листа
KERF = 4.0       # пропил фрезы между деталями


class _Rect:
    __slots__ = ("x", "y", "w", "h")

    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


@dataclass
class Placement:
    name: str
    x: float
    y: float
    w: float
    h: float
    rotated: bool


class _Bin:
    """Один лист. Алгоритм MaxRects (Best Short Side Fit)."""

    def __init__(self, w: float, h: float):
        self.w, self.h = w, h
        self.free: List[_Rect] = [_Rect(0, 0, w, h)]
        self.placed: List[Placement] = []

    def insert(self, w: float, h: float, name: str,
               allow_rotate: bool = True) -> Optional[Placement]:
        best = None  # (score_tuple, x, y, w, h, rotated)
        options = [(w, h, False)]
        if allow_rotate and abs(w - h) > 1e-6:
            options.append((h, w, True))
        for fr in self.free:
            for rw, rh, rot in options:
                if rw <= fr.w + 1e-6 and rh <= fr.h + 1e-6:
                    leftover_h = fr.w - rw
                    leftover_v = fr.h - rh
                    score = (min(leftover_h, leftover_v), max(leftover_h, leftover_v))
                    if best is None or score < best[0]:
                        best = (score, fr.x, fr.y, rw, rh, rot)
        if best is None:
            return None
        _, x, y, rw, rh, rot = best
        node = _Rect(x, y, rw, rh)
        new_free: List[_Rect] = []
        for fr in self.free:
            if self._overlaps(fr, node):
                new_free.extend(self._split(fr, node))
            else:
                new_free.append(fr)
        self.free = self._prune(new_free)
        placement = Placement(name, x, y, rw, rh, rot)
        self.placed.append(placement)
        return placement

    @staticmethod
    def _overlaps(f: _Rect, n: _Rect) -> bool:
        return not (n.x >= f.x + f.w - 1e-9 or n.x + n.w <= f.x + 1e-9 or
                    n.y >= f.y + f.h - 1e-9 or n.y + n.h <= f.y + 1e-9)

    @staticmethod
    def _split(f: _Rect, n: _Rect) -> List[_Rect]:
        res: List[_Rect] = []
        if n.x > f.x + 1e-9:
            res.append(_Rect(f.x, f.y, n.x - f.x, f.h))
        if n.x + n.w < f.x + f.w - 1e-9:
            res.append(_Rect(n.x + n.w, f.y, f.x + f.w - (n.x + n.w), f.h))
        if n.y > f.y + 1e-9:
            res.append(_Rect(f.x, f.y, f.w, n.y - f.y))
        if n.y + n.h < f.y + f.h - 1e-9:
            res.append(_Rect(f.x, n.y + n.h, f.w, f.y + f.h - (n.y + n.h)))
        return res

    @staticmethod
    def _prune(rects: List[_Rect]) -> List[_Rect]:
        keep: List[_Rect] = []
        for i, a in enumerate(rects):
            if a.w <= 1e-6 or a.h <= 1e-6:
                continue
            contained = False
            for j, b in enumerate(rects):
                if i == j:
                    continue
                if (a.x >= b.x - 1e-6 and a.y >= b.y - 1e-6 and
                        a.x + a.w <= b.x + b.w + 1e-6 and
                        a.y + a.h <= b.y + b.h + 1e-6):
                    same = (abs(a.w - b.w) < 1e-6 and abs(a.h - b.h) < 1e-6 and
                            abs(a.x - b.x) < 1e-6 and abs(a.y - b.y) < 1e-6)
                    if not (same and i < j):
                        contained = True
                        break
            if not contained:
                keep.append(a)
        return keep


def pack(items: List[Tuple[float, float, str]],
         sheet_w: float = SHEET_W, sheet_h: float = SHEET_H,
         margin: float = MARGIN, kerf: float = KERF) -> dict:
    """items: список (длина, ширина, имя). FFD: сортировка по площади убыв."""
    usable_w = sheet_w - 2 * margin
    usable_h = sheet_h - 2 * margin

    ordered = sorted(
        enumerate(items),
        key=lambda it: max(it[1][0], it[1][1]),  # сначала по длинной стороне
        reverse=True,
    )

    bins: List[_Bin] = []
    oversize: List[str] = []
    total_part_area = 0.0

    for _, (w, h, name) in ordered:
        # +kerf на пропил вокруг детали
        pw, ph = w + kerf, h + kerf
        if min(pw, ph) > max(usable_w, usable_h) or \
                (max(pw, ph) > max(usable_w, usable_h) and min(pw, ph) > min(usable_w, usable_h)):
            oversize.append(name)
            continue
        total_part_area += w * h
        placed = None
        for b in bins:
            placed = b.insert(pw, ph, name)
            if placed:
                break
        if not placed:
            b = _Bin(usable_w, usable_h)
            placed = b.insert(pw, ph, name)
            if placed:
                bins.append(b)
            else:
                oversize.append(name)
                total_part_area -= w * h

    sheets = []
    for b in bins:
        placements = []
        for p in b.placed:
            placements.append(Placement(
                p.name, p.x + margin, p.y + margin,
                p.w - kerf, p.h - kerf, p.rotated,
            ))
        sheets.append(placements)

    sheet_area = sheet_w * sheet_h
    utilization = (total_part_area / (len(sheets) * sheet_area) * 100.0) if sheets else 0.0

    return {
        "sheets": sheets,
        "sheet_count": len(sheets),
        "utilization": round(utilization, 1),
        "oversize": oversize,
        "sheet_w": sheet_w,
        "sheet_h": sheet_h,
    }


_PALETTE = ["#7cb6ff", "#9ad29a", "#ffd27c", "#ff9b9b", "#c8a6ff",
            "#7fe0d4", "#ffb3de", "#d0d97c"]


def render_cutmap_svg(pack_result: dict) -> str:
    """Рисуем все листы вертикальным стеком в одном SVG (масштаб ~ под 720px ширины)."""
    sheet_w = pack_result["sheet_w"]
    sheet_h = pack_result["sheet_h"]
    sheets = pack_result["sheets"]
    if not sheets:
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="40">'
                '<text x="4" y="24" font-size="14">Нет деталей для раскроя</text></svg>')

    scale = 720.0 / sheet_w
    pad = 16
    sw = sheet_w * scale
    sh = sheet_h * scale
    total_h = (sh + 40) * len(sheets) + pad
    color_for: dict = {}

    parts_svg = []
    parts_svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{sw + 2*pad:.0f}" '
        f'height="{total_h:.0f}" font-family="sans-serif">')

    y_off = pad
    for idx, placements in enumerate(sheets):
        parts_svg.append(
            f'<text x="{pad}" y="{y_off-4:.0f}" font-size="13" fill="#333">'
            f'Лист {idx+1} — {sheet_w:.0f}×{sheet_h:.0f} мм</text>')
        parts_svg.append(
            f'<rect x="{pad}" y="{y_off:.0f}" width="{sw:.1f}" height="{sh:.1f}" '
            f'fill="#fafafa" stroke="#333" stroke-width="1.5"/>')
        for p in placements:
            base = p.name.split("||")[-1].split(" (")[0]
            color = color_for.setdefault(base, _PALETTE[len(color_for) % len(_PALETTE)])
            rx = pad + p.x * scale
            ry = y_off + p.y * scale
            rw = p.w * scale
            rh = p.h * scale
            parts_svg.append(
                f'<rect x="{rx:.1f}" y="{ry:.1f}" width="{rw:.1f}" height="{rh:.1f}" '
                f'fill="{color}" fill-opacity="0.65" stroke="#222" stroke-width="0.6"/>')
            if rw > 46 and rh > 16:
                label = base if len(base) < int(rw / 6) else base[:max(3, int(rw/6))] + "…"
                parts_svg.append(
                    f'<text x="{rx + rw/2:.1f}" y="{ry + rh/2 + 3:.1f}" '
                    f'font-size="9" text-anchor="middle" fill="#111">{_esc(label)}</text>')
        y_off += sh + 40

    parts_svg.append("</svg>")
    return "".join(parts_svg)


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
