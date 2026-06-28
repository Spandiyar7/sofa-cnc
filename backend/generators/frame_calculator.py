"""Параметрический расчёт деталей каркаса дивана.

Все размеры — в миллиметрах (float). На вход: конфигурация (тип, подлокотники,
спинка, секции) + параметры оператора (длина, глубина, высоты, материал,
толщина листа). На выход — список деталей Part, сгруппированных по блокам.

Каждая деталь описывается плоским прямоугольником length × width, который потом
кладётся на лист раскроя и превращается в DXF. thickness — толщина материала
(вне плоскости листа), хранится как метаданные.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import List

# ------------------- конструктивные константы (мм) -------------------
LEG_HEIGHT = 80.0          # высота ножки, если ножки есть
ARMREST_RAISE = 150.0      # подлокотник выше сиденья на столько
ARMREST_TOP_WIDTH = 120.0  # ширина верхней крышки подлокотника
RAIL_BOTTOM_WIDTH = 60.0   # ширина рейки дна
RAIL_BOTTOM_PITCH = 120.0  # шаг реек дна
RAIL_BOTTOM_GAP = 20.0     # рейка дна короче проёма на столько
BACK_GUIDE_H = 80.0        # высота направляющих спинки
BACK_STILE_W = 80.0        # ширина боковой стойки спинки
BACK_CROSS_H = 60.0        # высота поперечины спинки
BACK_CROSS_PITCH = 200.0   # шаг поперечин спинки
CENTER_RAIL_PITCH = 600.0  # центральные поперечины основания: floor(L/600)-1
LEG_SIZE = 50.0            # сечение ножки/бруса


# Толще этого считаем деталь не листовой (массив/брус) — на роутер не идёт.
SHEET_MAX_THICKNESS = 30.0


@dataclass
class Part:
    """Одна деталь каркаса."""

    name: str          # человекочитаемое название (рус.)
    slug: str          # латиница, для имени DXF-файла
    qty: int
    length: float
    width: float
    thickness: float
    material: str
    block: str = "Основной блок"
    is_sheet: bool = True   # True = листовая деталь (фрезеруется); False = массив/брус

    def is_routable(self) -> bool:
        """Идёт ли деталь на листовой раскрой и фрезеровку."""
        return self.is_sheet and self.thickness <= SHEET_MAX_THICKNESS

    def key(self):
        """Ключ для слияния одинаковых деталей из разных блоков."""
        return (self.slug, round(self.length, 1), round(self.width, 1),
                round(self.thickness, 1))

    def to_dict(self) -> dict:
        return asdict(self)


def _is_plywood(material: str) -> bool:
    m = (material or "").lower()
    return "фанер" in m or "mdf" in m or "мдф" in m


def _block_parts(block: str, length: float, depth: float, h_seat: float,
                 h_back: float, t: float, material: str, legs: bool,
                 armrests_count: int, with_back: bool = True) -> List[Part]:
    """Детали одного прямого блока каркаса (основание + дно + спинка + подлокотники)."""
    parts: List[Part] = []
    leg_h = LEG_HEIGHT if legs else 0.0
    h_tsarga = max(80.0, h_seat - leg_h)          # высота царги
    inner_len = max(50.0, length - 2 * t)         # проём по длине
    inner_depth = max(50.0, depth - 2 * t)        # проём по глубине

    # --- Основание / рама (царги) ---
    parts.append(Part("Царга передняя", "tsarga_perednyaya", 1,
                      inner_len, h_tsarga, t, material, block))
    parts.append(Part("Царга задняя", "tsarga_zadnyaya", 1,
                      inner_len, h_tsarga, t, material, block))
    parts.append(Part("Царга боковая", "tsarga_bok", 2,
                      inner_depth, h_tsarga, t, material, block))

    n_center = max(0, math.floor(length / CENTER_RAIL_PITCH) - 1)
    if n_center:
        parts.append(Part("Поперечина основания", "poperechina_osnov", n_center,
                          inner_depth, h_tsarga, t, material, block))

    # --- Дно сиденья ---
    if _is_plywood(material):
        sheet_t = min(t, 18.0)
        parts.append(Part("Дно сиденья (лист)", "dno_list", 1,
                          inner_len, inner_depth, sheet_t, material, block))
    else:
        n_rails = max(1, math.floor(inner_len / RAIL_BOTTOM_PITCH))
        rail_len = max(50.0, inner_depth - RAIL_BOTTOM_GAP)
        parts.append(Part("Рейка дна", "reyka_dna", n_rails,
                          rail_len, RAIL_BOTTOM_WIDTH, t, material, block))

    # --- Спинка ---
    if with_back:
        back_h = max(120.0, h_back - h_seat)
        parts.append(Part("Направляющая спинки нижняя", "spinka_naprav_niz", 1,
                          inner_len, BACK_GUIDE_H, t, material, block))
        parts.append(Part("Направляющая спинки верхняя", "spinka_naprav_verh", 1,
                          inner_len, BACK_GUIDE_H, t, material, block))
        parts.append(Part("Стойка спинки боковая", "spinka_stoyka", 2,
                          back_h, BACK_STILE_W, t, material, block))
        n_cross = max(0, math.floor(back_h / BACK_CROSS_PITCH) - 1)
        if n_cross:
            parts.append(Part("Поперечина спинки", "spinka_poperechina", n_cross,
                              inner_len, BACK_CROSS_H, t, material, block))

    # --- Подлокотники ---
    if armrests_count > 0:
        arm_h = h_seat + ARMREST_RAISE
        parts.append(Part("Боковая панель подлокотника", "podlokot_panel",
                          armrests_count, depth, arm_h, t, material, block))
        parts.append(Part("Крышка подлокотника", "podlokot_kryshka",
                          armrests_count, depth, ARMREST_TOP_WIDTH, t, material, block))
        parts.append(Part("Стенка подлокотника", "podlokot_stenka",
                          armrests_count * 2, arm_h, ARMREST_TOP_WIDTH, t, material, block))

    # --- Ножки / опоры ---
    if legs:
        n_legs = 6 if length > 2000 else 4
        parts.append(Part("Ножка", "nozhka", n_legs,
                          leg_h or LEG_HEIGHT, LEG_SIZE, LEG_SIZE, material, block,
                          is_sheet=False))

    return parts


def _corner_module(depth: float, h_seat: float, h_back: float, t: float,
                   material: str, legs: bool) -> List[Part]:
    """Угловой модуль — квадрат W×W: рама + дно + две стойки спинки."""
    block = "Угловой модуль"
    parts: List[Part] = []
    leg_h = LEG_HEIGHT if legs else 0.0
    h_tsarga = max(80.0, h_seat - leg_h)
    side = max(50.0, depth - 2 * t)

    parts.append(Part("Царга угла", "ugol_tsarga", 4,
                      side, h_tsarga, t, material, block))
    if _is_plywood(material):
        parts.append(Part("Дно угла (лист)", "ugol_dno_list", 1,
                          side, side, min(t, 18.0), material, block))
    else:
        n_rails = max(1, math.floor(side / RAIL_BOTTOM_PITCH))
        parts.append(Part("Рейка дна угла", "ugol_reyka", n_rails,
                          max(50.0, side - RAIL_BOTTOM_GAP), RAIL_BOTTOM_WIDTH,
                          t, material, block))
    back_h = max(120.0, h_back - h_seat)
    parts.append(Part("Стойка спинки угла", "ugol_stoyka", 2,
                      back_h, BACK_STILE_W, t, material, block))
    if legs:
        parts.append(Part("Ножка угла", "ugol_nozhka", 1,
                          leg_h or LEG_HEIGHT, LEG_SIZE, LEG_SIZE, material, block,
                          is_sheet=False))
    return parts


def calculate(config: dict, params: dict) -> List[Part]:
    """Главная точка входа. Возвращает плоский список Part (с дубликатами по блокам)."""
    sofa_type = config.get("type", "straight")
    armrests = config.get("armrests", "both")
    legs = bool(config.get("legs_visible", True))

    depth = float(params["depth"])
    h_seat = float(params["height_seat"])
    h_back = float(params["height_back"])
    t = float(params["thickness"])
    material = params["material"]

    arm_count = {"both": 2, "left": 1, "right": 1, "none": 0}.get(armrests, 2)
    parts: List[Part] = []

    if sofa_type == "armchair":
        parts += _block_parts("Кресло", float(params["length"]), depth, h_seat,
                              h_back, t, material, legs, arm_count)

    elif sofa_type == "straight":
        parts += _block_parts("Основной блок", float(params["length"]), depth,
                              h_seat, h_back, t, material, legs, arm_count)

    elif sofa_type == "corner_l":
        l1 = float(params["length"])
        l2 = float(params.get("length2") or params["length"])
        # внешние подлокотники: по одному на каждый крайний блок
        parts += _block_parts("Блок A", l1, depth, h_seat, h_back, t, material,
                              legs, 1 if arm_count else 0)
        parts += _corner_module(depth, h_seat, h_back, t, material, legs)
        parts += _block_parts("Блок Б", l2, depth, h_seat, h_back, t, material,
                              legs, 1 if arm_count else 0)

    elif sofa_type == "corner_u":
        l1 = float(params["length"])
        l2 = float(params.get("length2") or params["length"])
        l3 = float(params.get("length3") or params["length"])
        parts += _block_parts("Левый блок", l1, depth, h_seat, h_back, t,
                              material, legs, 1 if arm_count else 0)
        parts += _corner_module(depth, h_seat, h_back, t, material, legs)
        parts += _block_parts("Центральный блок", l2, depth, h_seat, h_back, t,
                              material, legs, 0)
        parts += _corner_module(depth, h_seat, h_back, t, material, legs)
        parts += _block_parts("Правый блок", l3, depth, h_seat, h_back, t,
                              material, legs, 1 if arm_count else 0)
    else:
        parts += _block_parts("Основной блок", float(params["length"]), depth,
                              h_seat, h_back, t, material, legs, arm_count)

    return parts


def merge_parts(parts: List[Part]) -> List[Part]:
    """Сливаем одинаковые детали (по геометрии) в одну строку, суммируя количество."""
    merged: dict = {}
    order: List = []
    for p in parts:
        k = p.key()
        if k in merged:
            merged[k].qty += p.qty
        else:
            clone = Part(p.name, p.slug, p.qty, p.length, p.width,
                         p.thickness, p.material, p.block, p.is_sheet)
            merged[k] = clone
            order.append(k)
    return [merged[k] for k in order]
