"""Модели БД (SQLModel/SQLite) для хранения заказов."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlmodel import Field, SQLModel, create_engine

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "orders.db"
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


class Order(SQLModel, table=True):
    """Один заказ цеха: тип дивана, размеры, материал и параметры генерации."""

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    sofa_type: str
    material: str
    length: float        # общая длина, мм
    width: float         # глубина, мм
    height: float        # высота спинки, мм
    sheets: int = 0      # сколько листов материала нужно
    parts_count: int = 0
    status: str = "new"  # new | done | archived
    config_json: str = "{}"  # результат анализа AI (тип, подлокотники, ...)
    params_json: str = "{}"  # полный набор параметров для повторной генерации


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
