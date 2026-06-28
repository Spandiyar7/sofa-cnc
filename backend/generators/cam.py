"""CAM-слой: траектории фрезеровки 2.5D + генерация G-code (NCStudio / RichAuto / DSP).

Из геометрии детали (контур + отверстия) на её позиции в раскрое строится:
  • сверловка отверстий (присадка) — пошаговый клевок (peck);
  • контурная обрезка по внешнему контуру со смещением на радиус фрезы,
    в несколько проходов по Z, с перемычками (tabs), чтобы деталь не вырвало;
  • вывод в ISO G-code, который понимают NCStudio и RichAuto/DSP.

Один .nc на лист раскроя: оператор кладёт лист → запускает программу → снимает все детали.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class CamSettings:
    tool_diameter: float = 6.0      # диаметр фрезы, мм
    spindle_rpm: int = 18000        # обороты шпинделя
    feed_xy: float = 3000.0         # подача резания, мм/мин
    feed_z: float = 1000.0          # подача врезания, мм/мин
    pass_depth: float = 6.0         # глубина за проход, мм
    safe_z: float = 8.0             # безопасная высота холостых, мм
    clearance_z: float = 2.0        # высота отскока над заготовкой, мм
    cut_through: float = 0.5        # прорезать на столько ниже детали (в жертвенный лист)
    tab_height: float = 4.0         # высота перемычки (остаётся материала снизу), мм
    tab_width: float = 10.0         # ширина перемычки вдоль контура, мм
    drill_peck: float = 5.0         # шаг клевка при сверлении, мм
    dwell: float = 1.0              # пауза на разгон шпинделя, сек


def _f(v: float) -> str:
    """Число для G-code: 3 знака, без хвостовых нулей."""
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


@dataclass
class _Plot:
    cuts: List[Tuple[float, float, float, float]]   # рабочие ходы (x0,y0,x1,y1)
    rapids: List[Tuple[float, float, float, float]]  # холостые
    holes: List[Tuple[float, float, float]]          # (x,y,d)


def transform_hole(hx: float, hy: float, part_len: float, part_wid: float,
                   px: float, py: float, rotated: bool) -> Tuple[float, float]:
    """Координата отверстия детали → координата на листе (с учётом поворота 90°)."""
    if not rotated:
        return px + hx, py + hy
    # поворот на 90°: (hx,hy) в боксе L×W -> (hy, L-hx) в боксе W×L
    return px + hy, py + (part_len - hx)


def _drill(lines: List[str], plot: _Plot, holes_xy: List[Tuple[float, float, float]],
           depth: float, s: CamSettings, last_xy: List[float]) -> None:
    for (x, y, d) in holes_xy:
        lines.append(f"G0 Z{_f(s.safe_z)}")
        lines.append(f"G0 X{_f(x)} Y{_f(y)}")
        if last_xy[0] is not None:
            plot.rapids.append((last_xy[0], last_xy[1], x, y))
        last_xy[0], last_xy[1] = x, y
        lines.append(f"G0 Z{_f(s.clearance_z)}")
        z = 0.0
        while z > -depth + 1e-6:
            z = max(-depth, z - s.drill_peck)
            lines.append(f"G1 Z{_f(z)} F{_f(s.feed_z)}")
            if z > -depth + 1e-6:
                lines.append(f"G0 Z{_f(s.clearance_z)}")  # отскок, сброс стружки
        lines.append(f"G0 Z{_f(s.safe_z)}")
        plot.holes.append((x, y, d))


def _cut_edge(lines: List[str], plot: _Plot, x0, y0, x1, y1, z, needs_tab, tab_z,
              s: CamSettings) -> None:
    """Рез ребра прямоугольника от (x0,y0) к (x1,y1) на глубине z, с перемычкой при необходимости."""
    seg_len = math.hypot(x1 - x0, y1 - y0)
    if not needs_tab or seg_len <= s.tab_width + 4:
        lines.append(f"G1 X{_f(x1)} Y{_f(y1)} F{_f(s.feed_xy)}")
        plot.cuts.append((x0, y0, x1, y1))
        return
    ux, uy = (x1 - x0) / seg_len, (y1 - y0) / seg_len
    mid = seg_len / 2.0
    a = mid - s.tab_width / 2.0
    b = mid + s.tab_width / 2.0
    ax, ay = x0 + ux * a, y0 + uy * a
    bx, by = x0 + ux * b, y0 + uy * b
    lines.append(f"G1 X{_f(ax)} Y{_f(ay)} F{_f(s.feed_xy)}")          # до перемычки
    plot.cuts.append((x0, y0, ax, ay))
    lines.append(f"G1 Z{_f(tab_z)}")                                  # подъём над перемычкой
    lines.append(f"G1 X{_f(bx)} Y{_f(by)}")                           # переезд над перемычкой
    plot.cuts.append((ax, ay, bx, by))
    lines.append(f"G1 Z{_f(z)}")                                      # назад на глубину
    lines.append(f"G1 X{_f(x1)} Y{_f(y1)}")
    plot.cuts.append((bx, by, x1, y1))


def _profile(lines: List[str], plot: _Plot, px, py, pw, ph, thickness: float,
             s: CamSettings, last_xy: List[float]) -> None:
    r = s.tool_diameter / 2.0
    x0, y0 = px - r, py - r            # контур со смещением наружу на радиус фрезы
    x1, y1 = px + pw + r, py + ph + r
    total_depth = thickness + s.cut_through
    tab_h = min(s.tab_height, thickness - 1.0)
    tab_z = -(thickness - tab_h)
    n = max(1, math.ceil(total_depth / s.pass_depth))

    lines.append(f"G0 Z{_f(s.safe_z)}")
    lines.append(f"G0 X{_f(x0)} Y{_f(y0)}")
    if last_xy[0] is not None:
        plot.rapids.append((last_xy[0], last_xy[1], x0, y0))
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    for i in range(n):
        z = -min(total_depth, s.pass_depth * (i + 1))
        needs_tab = z < tab_z - 1e-6
        lines.append(f"G1 Z{_f(z)} F{_f(s.feed_z)}")   # врезание в углу
        for j in range(4):
            cx0, cy0 = corners[j]
            cx1, cy1 = corners[j + 1]
            _cut_edge(lines, plot, cx0, cy0, cx1, cy1, z, needs_tab, tab_z, s)
    lines.append(f"G0 Z{_f(s.safe_z)}")
    last_xy[0], last_xy[1] = x0, y0


def gcode_for_sheet(placements, parts_by_idx, joint_holes_fn, settings: CamSettings,
                    material: str, sheet_w: float, sheet_h: float,
                    sheet_index: int) -> dict:
    """Строит G-code и данные симуляции для одного листа.

    placements: список Placement (из bin_packing), name = "idx||Имя".
    parts_by_idx: dict idx -> Part.
    joint_holes_fn: функция (part) -> [(hx,hy,d), ...] отверстий в координатах детали.
    """
    s = settings
    lines: List[str] = []
    plot = _Plot(cuts=[], rapids=[], holes=[])
    last_xy: List[float] = [None, None]

    lines.append(f"(Sofa-CNC | лист {sheet_index + 1} | {material})")
    lines.append(f"(Лист {sheet_w:.0f}x{sheet_h:.0f} мм | фреза D{_f(s.tool_diameter)} | "
                 f"шпиндель {s.spindle_rpm} | подача {s.feed_xy:.0f})")
    lines.append("G90 G21 G17")          # абсолют, мм, плоскость XY
    lines.append("G54")                  # рабочая система координат
    lines.append(f"G0 Z{_f(s.safe_z)}")
    lines.append(f"M03 S{s.spindle_rpm}")
    lines.append(f"G04 P{_f(s.dwell)}")

    n_holes = 0
    for p in placements:
        idx = int(p.name.split("||")[0])
        part = parts_by_idx[idx]
        # 1) сверловка
        holes_local = joint_holes_fn(part)
        holes_xy = [(*transform_hole(hx, hy, part.length, part.width, p.x, p.y, p.rotated), d)
                    for (hx, hy, d) in holes_local]
        if holes_xy:
            lines.append(f"(деталь: {part.name} — отверстий {len(holes_xy)})")
            _drill(lines, plot, holes_xy, part.thickness + s.cut_through, s, last_xy)
            n_holes += len(holes_xy)
        # 2) контурная обрезка
        lines.append(f"(деталь: {part.name} — обрезка контура)")
        _profile(lines, plot, p.x, p.y, p.w, p.h, part.thickness, s, last_xy)

    lines.append(f"G0 Z{_f(s.safe_z)}")
    lines.append("M05")
    lines.append("M30")
    nc = "\n".join(lines) + "\n"

    cut_len = sum(math.hypot(c[2] - c[0], c[3] - c[1]) for c in plot.cuts)
    rapid_len = sum(math.hypot(c[2] - c[0], c[3] - c[1]) for c in plot.rapids)
    est_min = cut_len / max(1.0, s.feed_xy) + (n_holes * part_drill_time(s)) if placements else 0.0

    return {
        "nc": nc,
        "backplot": plot,
        "sheet_w": sheet_w,
        "sheet_h": sheet_h,
        "stats": {
            "cut_len_m": round(cut_len / 1000.0, 1),
            "rapid_len_m": round(rapid_len / 1000.0, 1),
            "holes": n_holes,
            "est_min": round(est_min, 1),
            "lines": len(lines),
        },
    }


def part_drill_time(s: CamSettings) -> float:
    """Грубая оценка времени на одно отверстие, мин (через/клевок)."""
    return 0.05


def render_backplot_svg(result: dict) -> str:
    """SVG-симуляция траектории листа: рез — синий, холостые — серый пунктир, отверстия — красные."""
    sw, sh = result["sheet_w"], result["sheet_h"]
    plot: _Plot = result["backplot"]
    scale = 720.0 / sw
    pad = 14
    W = sw * scale + 2 * pad
    H = sh * scale + 2 * pad

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" '
           f'font-family="sans-serif">']
    out.append(f'<rect x="{pad}" y="{pad}" width="{sw*scale:.1f}" height="{sh*scale:.1f}" '
               f'fill="#0f1726" stroke="#33415c" stroke-width="1"/>')

    def tx(x): return pad + x * scale
    def ty(y): return pad + y * scale

    for (x0, y0, x1, y1) in plot.rapids:
        out.append(f'<line x1="{tx(x0):.1f}" y1="{ty(y0):.1f}" x2="{tx(x1):.1f}" '
                   f'y2="{ty(y1):.1f}" stroke="#5a6b8c" stroke-width="0.5" '
                   f'stroke-dasharray="3 3"/>')
    for (x0, y0, x1, y1) in plot.cuts:
        out.append(f'<line x1="{tx(x0):.1f}" y1="{ty(y0):.1f}" x2="{tx(x1):.1f}" '
                   f'y2="{ty(y1):.1f}" stroke="#38bdf8" stroke-width="1.1"/>')
    for (x, y, d) in plot.holes:
        out.append(f'<circle cx="{tx(x):.1f}" cy="{ty(y):.1f}" r="{max(1.5, d/2*scale):.1f}" '
                   f'fill="none" stroke="#f87171" stroke-width="1"/>')
    out.append("</svg>")
    return "".join(out)
