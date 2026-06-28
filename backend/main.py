"""FastAPI-приложение: анализ фото, генерация DXF, история заказов.

Запуск:  uvicorn main:app --reload  (из папки backend)
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import Session, select

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
FRONTEND_DIR = ROOT_DIR / "frontend"
LOG_DIR = ROOT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT_DIR / ".env")

# ---- логирование каждой генерации в файл ----
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "generations.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logging.getLogger("ezdxf").setLevel(logging.WARNING)  # убираем шум ezdxf из лога
logger = logging.getLogger("sofa-cnc")

from models import Order, engine, init_db  # noqa: E402
from vision import analyze_photo  # noqa: E402
from generators.frame_calculator import calculate, merge_parts  # noqa: E402
from generators.dxf_generator import build_dxf_zip, compute_holes  # noqa: E402
from generators.svg_preview import part_preview_svg  # noqa: E402
from generators import bin_packing  # noqa: E402
from generators import cam  # noqa: E402

app = FastAPI(title="Платформа чертежей каркасов дивана для ЧПУ")

# Кэш сгенерированных ZIP в памяти: download_id -> bytes
_ZIP_CACHE: dict[str, bytes] = {}

MATERIAL_THICKNESS = {
    "Фанера берёзовая": 18.0,
    "Фанера хвойная": 18.0,
    "Брус сосновый": 40.0,
    "ДСП 16мм": 16.0,
    "ДСП 22мм": 22.0,
    "MDF 16мм": 16.0,
}


class GenerateRequest(BaseModel):
    sofa_type: str = "straight"
    armrests: str = "both"
    backrest: str = "straight"
    chaise: bool = False
    chaise_side: Optional[str] = None
    sections: int = 1
    legs: bool = True

    length: float
    length2: Optional[float] = None
    length3: Optional[float] = None
    depth: float
    height_back: float
    height_seat: float

    material: str = "Фанера берёзовая"
    thickness: float = 18.0
    joint: str = "Шкант"

    # --- параметры ЧПУ (CAM / G-code) ---
    gcode: bool = True
    tool_diameter: float = 6.0
    spindle_rpm: int = 18000
    feed_xy: float = 3000.0
    feed_z: float = 1000.0
    pass_depth: float = 6.0
    tab_height: float = 4.0
    tab_width: float = 10.0


@app.on_event("startup")
def _startup() -> None:
    init_db()
    logger.info("БД инициализирована, приложение запущено")


# --------------------------- API ---------------------------
@app.post("/api/analyze-photo")
async def api_analyze_photo(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "Пустой файл")
    media_type = file.content_type or "image/jpeg"
    if media_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        media_type = "image/jpeg"
    config = analyze_photo(data, media_type)
    logger.info("Анализ фото '%s' -> %s (%s)",
                file.filename, config.get("type"), config.get("_source"))
    return config


def _validate(req: GenerateRequest) -> None:
    if req.sofa_type == "armchair":
        if not (300 <= req.length <= 1000):
            raise HTTPException(422, "Для кресла длина должна быть 300–1000 мм")
    else:
        if not (600 <= req.length <= 5000):
            raise HTTPException(422, "Длина должна быть в диапазоне 600–5000 мм")
    if not (300 <= req.depth <= 1500):
        raise HTTPException(422, "Глубина должна быть в диапазоне 300–1500 мм")
    if not (req.height_back > req.height_seat):
        raise HTTPException(422, "Высота спинки должна быть больше высоты сиденья")
    if not (5 <= req.thickness <= 60):
        raise HTTPException(422, "Толщина материала должна быть 5–60 мм")
    if req.gcode:
        if not (1 <= req.tool_diameter <= 25):
            raise HTTPException(422, "Диаметр фрезы должен быть 1–25 мм")
        if not (0.5 <= req.pass_depth <= req.thickness + 1):
            raise HTTPException(422, "Глубина за проход должна быть 0.5 мм … толщина материала")
        if req.feed_xy <= 0 or req.feed_z <= 0:
            raise HTTPException(422, "Подачи должны быть больше нуля")


def _run_pipeline(req: GenerateRequest) -> dict:
    """Полный цикл: расчёт деталей -> раскрой -> превью -> DXF ZIP."""
    config = {
        "type": req.sofa_type,
        "armrests": req.armrests,
        "backrest": req.backrest,
        "chaise": req.chaise,
        "sections": req.sections,
        "legs_visible": req.legs,
    }
    params = {
        "length": req.length,
        "length2": req.length2,
        "length3": req.length3,
        "depth": req.depth,
        "height_back": req.height_back,
        "height_seat": req.height_seat,
        "material": req.material,
        "thickness": req.thickness,
    }

    raw_parts = calculate(config, params)
    merged = merge_parts(raw_parts)

    # Зазор раскроя: для фрезеровки минимум = диаметр фрезы + 2 мм (иначе резы соседних
    # деталей сольются). Для пилы достаточно пропила.
    gap = max(4.0, req.tool_diameter + 2.0) if req.gcode else 4.0

    # На листовой раскрой и фрезеровку идут только листовые детали; ножки/брус — отдельно
    routable = [p for p in merged if p.is_routable()]
    solid = [p for p in merged if not p.is_routable()]

    # Раскрой: разворачиваем количество в отдельные детали с уникальным id "i||Имя"
    expanded = [p for p in routable for _ in range(p.qty)]
    items = [(p.length, p.width, f"{i}||{p.name}") for i, p in enumerate(expanded)]
    pack_result = bin_packing.pack(items, kerf=gap)
    cutmap_svg = bin_packing.render_cutmap_svg(pack_result)

    previews = [{"name": p.name, "block": p.block, "svg": part_preview_svg(p, req.joint)}
                for p in routable]

    parts_table = [{
        "name": p.name, "block": p.block, "qty": p.qty,
        "length": round(p.length, 1), "width": round(p.width, 1),
        "thickness": round(p.thickness, 1), "material": p.material,
        "stock": "лист" if p.is_routable() else "массив/брус",
    } for p in merged]

    # --- CAM: траектории + G-code по листам ---
    gcode_sheets = []
    extra_files = {}
    cam_totals = {"cut_len_m": 0.0, "holes": 0, "est_min": 0.0}
    if req.gcode and pack_result["sheet_count"] > 0:
        settings = cam.CamSettings(
            tool_diameter=req.tool_diameter, spindle_rpm=req.spindle_rpm,
            feed_xy=req.feed_xy, feed_z=req.feed_z, pass_depth=req.pass_depth,
            tab_height=req.tab_height, tab_width=req.tab_width,
        )
        parts_by_idx = {i: p for i, p in enumerate(expanded)}
        holes_fn = lambda part: compute_holes(part.length, part.width,
                                              part.thickness, req.joint)
        for n, placements in enumerate(pack_result["sheets"]):
            res = cam.gcode_for_sheet(
                placements, parts_by_idx, holes_fn, settings, req.material,
                pack_result["sheet_w"], pack_result["sheet_h"], n)
            extra_files[f"gcode/sheet_{n+1}.nc"] = res["nc"].encode("utf-8")
            gcode_sheets.append({
                "sheet": n + 1,
                "svg": cam.render_backplot_svg(res),
                "stats": res["stats"],
            })
            cam_totals["cut_len_m"] += res["stats"]["cut_len_m"]
            cam_totals["holes"] += res["stats"]["holes"]
            cam_totals["est_min"] += res["stats"]["est_min"]
        cam_totals = {k: round(v, 1) if isinstance(v, float) else v
                      for k, v in cam_totals.items()}

    zip_bytes, filenames = build_dxf_zip(merged, req.joint, req.sofa_type, extra_files)
    download_id = uuid.uuid4().hex
    _ZIP_CACHE[download_id] = zip_bytes

    logger.info(
        "Генерация: тип=%s L=%.0f W=%.0f деталей=%d листов=%d использование=%.1f%% "
        "G-code=%s отверстий=%d рез=%.1fм ~%.1fмин",
        req.sofa_type, req.length, req.depth, len(merged),
        pack_result["sheet_count"], pack_result["utilization"],
        req.gcode, cam_totals["holes"], cam_totals["cut_len_m"], cam_totals["est_min"],
    )

    return {
        "download_id": download_id,
        "parts": parts_table,
        "parts_count": sum(p.qty for p in merged),
        "unique_parts": len(merged),
        "files": filenames,
        "previews": previews,
        "cutmap_svg": cutmap_svg,
        "sheets": pack_result["sheet_count"],
        "utilization": pack_result["utilization"],
        "oversize": pack_result["oversize"],
        "gcode_enabled": req.gcode,
        "gcode_sheets": gcode_sheets,
        "cam_totals": cam_totals,
        "solid_parts": [{"name": p.name, "qty": p.qty,
                         "size": f"{p.length:.0f}×{p.width:.0f}×{p.thickness:.0f}"}
                        for p in solid],
    }


@app.post("/api/generate-drawings")
def api_generate(req: GenerateRequest):
    _validate(req)
    return _run_pipeline(req)


@app.get("/api/download/{download_id}")
def api_download(download_id: str):
    data = _ZIP_CACHE.get(download_id)
    if data is None:
        raise HTTPException(404, "Архив не найден — сгенерируйте чертежи заново")
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="dxf_{download_id[:8]}.zip"'},
    )


class SaveOrderRequest(BaseModel):
    request: GenerateRequest
    config: dict = {}
    sheets: int = 0
    parts_count: int = 0


@app.post("/api/save-order")
def api_save_order(payload: SaveOrderRequest):
    req = payload.request
    order = Order(
        sofa_type=req.sofa_type,
        material=req.material,
        length=req.length,
        width=req.depth,
        height=req.height_back,
        sheets=payload.sheets,
        parts_count=payload.parts_count,
        status="done",
        config_json=json.dumps(payload.config, ensure_ascii=False),
        params_json=req.model_dump_json(),
    )
    with Session(engine) as session:
        session.add(order)
        session.commit()
        session.refresh(order)
    logger.info("Заказ #%s сохранён", order.id)
    return {"id": order.id, "created_at": order.created_at}


@app.get("/api/orders")
def api_orders():
    with Session(engine) as session:
        rows = session.exec(select(Order).order_by(Order.id.desc())).all()
    return [{
        "id": o.id, "created_at": o.created_at, "sofa_type": o.sofa_type,
        "material": o.material, "length": o.length, "width": o.width,
        "height": o.height, "sheets": o.sheets, "parts_count": o.parts_count,
        "status": o.status,
    } for o in rows]


@app.get("/api/orders/{order_id}")
def api_order(order_id: int):
    with Session(engine) as session:
        o = session.get(Order, order_id)
    if not o:
        raise HTTPException(404, "Заказ не найден")
    return {
        "id": o.id, "created_at": o.created_at, "sofa_type": o.sofa_type,
        "material": o.material, "length": o.length, "width": o.width,
        "height": o.height, "sheets": o.sheets, "parts_count": o.parts_count,
        "status": o.status,
        "config": json.loads(o.config_json or "{}"),
        "params": json.loads(o.params_json or "{}"),
    }


@app.get("/api/orders/{order_id}/download")
def api_order_download(order_id: int):
    with Session(engine) as session:
        o = session.get(Order, order_id)
    if not o:
        raise HTTPException(404, "Заказ не найден")
    req = GenerateRequest(**json.loads(o.params_json))
    _validate(req)
    result = _run_pipeline(req)
    data = _ZIP_CACHE.get(result["download_id"])
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="order_{order_id}.zip"'},
    )


@app.get("/api/materials")
def api_materials():
    return MATERIAL_THICKNESS


# --------------------------- Фронтенд ---------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    return (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/orders", response_class=HTMLResponse)
def orders_page():
    return (FRONTEND_DIR / "orders.html").read_text(encoding="utf-8")


# статика (app.js, style.css)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
