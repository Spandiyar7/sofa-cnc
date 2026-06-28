"""Генерация DXF-файлов деталей через ezdxf и упаковка их в ZIP.

Каждая уникальная деталь — отдельный DXF (масштаб 1:1, единицы — мм) со слоями:
  КОНТУР    — внешний замкнутый контур (LWPOLYLINE)
  РАЗМЕРЫ   — линейные размеры (DIMENSION)
  ТЕКСТ     — название, материал, количество, толщина
  ОТВЕРСТИЯ — круги под шканты/конфирматы/саморезы
"""
from __future__ import annotations

import io
import zipfile
from typing import List, Optional, Tuple

import ezdxf

from .frame_calculator import Part

# Карта типов соединений -> (диаметр, шаг). шип-паз -> без отверстий.
_JOINTS = {
    "шкант": (8.0, 150.0),
    "shkant": (8.0, 150.0),
    "dowel": (8.0, 150.0),
    "конфирмат": (5.0, 300.0),
    "konfirmat": (5.0, 300.0),
    "confirmat": (5.0, 300.0),
    "саморез": (4.5, 200.0),
    "samorez": (4.5, 200.0),
    "screw": (4.5, 200.0),
}

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", " ": "_",
}

TYPE_SLUG = {
    "straight": "pryamoy",
    "corner_l": "uglovoy",
    "corner_u": "p_obrazniy",
    "armchair": "kreslo",
}


def slugify(text: str) -> str:
    out = []
    for ch in text.lower():
        out.append(_TRANSLIT.get(ch, ch if ch.isalnum() or ch in "_-" else "_"))
    s = "".join(out)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_") or "detail"


def _joint_spec(joint: str):
    j = (joint or "").strip().lower()
    for key, spec in _JOINTS.items():
        if key in j:
            return spec, key
    return None, j


def compute_holes(length: float, width: float, thickness: float,
                  joint: str) -> List[Tuple[float, float, float]]:
    """Отверстия (x, y, d) вдоль двух длинных кромок детали (по длине)."""
    spec, key = _joint_spec(joint)
    if not spec:
        return []
    d, pitch = spec
    if "конфирмат" in key or "konfirmat" in key or "confirmat" in key:
        inset = 8.0
    else:
        inset = max(d, min(thickness / 2.0, 12.0))
    holes: List[Tuple[float, float, float]] = []
    if width < 2 * inset + 4:
        # узкая деталь — один ряд по центру
        y = width / 2.0
        x = 50.0
        while x <= length - 50.0 + 1e-6:
            holes.append((x, y, d))
            x += pitch
        return holes
    x = 50.0
    while x <= length - 50.0 + 1e-6:
        holes.append((x, inset, d))
        holes.append((x, width - inset, d))
        x += pitch
    return holes


def _build_doc(part: Part, joint: str):
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    for name, color in (("КОНТУР", 7), ("РАЗМЕРЫ", 1),
                        ("ТЕКСТ", 3), ("ОТВЕРСТИЯ", 5)):
        if name not in doc.layers:
            doc.layers.add(name, color=color)

    msp = doc.modelspace()
    L, W, T = part.length, part.width, part.thickness

    # КОНТУР — замкнутый прямоугольник
    msp.add_lwpolyline(
        [(0, 0), (L, 0), (L, W), (0, W)],
        close=True,
        dxfattribs={"layer": "КОНТУР"},
    )

    # ОТВЕРСТИЯ
    for (hx, hy, hd) in compute_holes(L, W, T, joint):
        msp.add_circle((hx, hy), hd / 2.0, dxfattribs={"layer": "ОТВЕРСТИЯ"})

    # РАЗМЕРЫ — линейные, текст масштабируем под габарит детали
    txt = max(15.0, max(L, W) / 30.0)
    off = max(40.0, max(L, W) / 12.0)
    try:
        dim_h = msp.add_linear_dim(
            base=(0, -off), p1=(0, 0), p2=(L, 0), angle=0,
            dimstyle="EZDXF",
            override={"dimtxt": txt, "dimasz": txt * 0.6, "dimexe": txt * 0.4},
            dxfattribs={"layer": "РАЗМЕРЫ"},
        )
        dim_h.render()
        dim_v = msp.add_linear_dim(
            base=(-off, 0), p1=(0, 0), p2=(0, W), angle=90,
            dimstyle="EZDXF",
            override={"dimtxt": txt, "dimasz": txt * 0.6, "dimexe": txt * 0.4},
            dxfattribs={"layer": "РАЗМЕРЫ"},
        )
        dim_v.render()
    except Exception:
        # если стиль размеров недоступен — продолжаем без размеров
        pass

    # ТЕКСТ — описание детали внутри контура
    lines = [
        part.name,
        f"Материал: {part.material}",
        f"Размер: {L:.0f} x {W:.0f} x {T:.0f} мм",
        f"Количество: {part.qty} шт",
        f"Соединение: {joint}",
    ]
    th = max(14.0, min(L, W) / 14.0)
    y = W / 2.0 + th * len(lines) / 2.0
    for line in lines:
        t = msp.add_text(line, height=th, dxfattribs={"layer": "ТЕКСТ"})
        t.dxf.insert = (max(8.0, L * 0.06), y)
        y -= th * 1.5

    return doc


def _doc_to_bytes(doc) -> bytes:
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


def build_dxf_zip(parts: List[Part], joint: str, sofa_type: str,
                  extra_files: Optional[dict] = None) -> Tuple[bytes, List[str]]:
    """Возвращает (zip_bytes, список_имён_файлов).

    По одному DXF на уникальную деталь (в папке dxf/) + дополнительные файлы
    extra_files {имя: bytes} (например, G-code .nc по листам в папке gcode/).
    """
    type_slug = TYPE_SLUG.get(sofa_type, "sofa")
    buf = io.BytesIO()
    names: List[str] = []
    used = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for part in parts:
            fname = f"{type_slug}_{part.slug}_{part.length:.0f}x{part.width:.0f}.dxf"
            base = fname
            i = 2
            while fname in used:
                fname = base.replace(".dxf", f"_{i}.dxf")
                i += 1
            used.add(fname)
            doc = _build_doc(part, joint)
            zf.writestr(f"dxf/{fname}", _doc_to_bytes(doc))
            names.append(fname)
        for name, data in (extra_files or {}).items():
            zf.writestr(name, data)
            names.append(name)
    return buf.getvalue(), names
