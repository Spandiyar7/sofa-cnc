"""Лёгкое SVG-превью отдельной детали (контур + отверстия + размеры).

Используется на экране результата, чтобы оператор глазами проверил деталь,
не открывая DXF в CAD.
"""
from __future__ import annotations

from .dxf_generator import compute_holes
from .frame_calculator import Part

BOX = 240.0  # максимальный габарит превью, px


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def part_preview_svg(part: Part, joint: str) -> str:
    L, W, T = part.length, part.width, part.thickness
    pad = 26.0
    scale = (BOX - 2 * pad) / max(L, W, 1.0)
    pw = L * scale
    ph = W * scale
    width = pw + 2 * pad
    height = ph + 2 * pad + 16

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
        f'height="{height:.0f}" font-family="sans-serif">',
        f'<rect x="{pad:.1f}" y="{pad:.1f}" width="{pw:.1f}" height="{ph:.1f}" '
        f'fill="#eef4ff" stroke="#2b5fb3" stroke-width="1.5"/>',
    ]

    for (hx, hy, hd) in compute_holes(L, W, T, joint):
        cx = pad + hx * scale
        cy = pad + (W - hy) * scale  # переворот Y, чтобы низ был внизу
        r = max(1.2, hd / 2.0 * scale)
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" '
                   f'fill="#fff" stroke="#c0392b" stroke-width="0.7"/>')

    # размерные подписи
    out.append(
        f'<text x="{pad + pw/2:.1f}" y="{pad + ph + 13:.1f}" font-size="11" '
        f'text-anchor="middle" fill="#333">{L:.0f} мм</text>')
    out.append(
        f'<text x="{pad - 6:.1f}" y="{pad + ph/2:.1f}" font-size="11" '
        f'text-anchor="middle" fill="#333" '
        f'transform="rotate(-90 {pad-6:.1f} {pad + ph/2:.1f})">{W:.0f} мм</text>')
    out.append(
        f'<text x="{pad + pw/2:.1f}" y="{pad/2 + 4:.1f}" font-size="11" '
        f'text-anchor="middle" fill="#111">{_esc(part.name)}</text>')
    out.append("</svg>")
    return "".join(out)
