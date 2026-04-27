"""Loads constitution YAML files from agent/constitutions/*.yaml at startup."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import yaml


CONSTITUTIONS_DIR = Path(__file__).resolve().parents[2] / "constitutions"


class ConstitutionNotFound(Exception):
    pass


@lru_cache(maxsize=None)
def load_constitution(constitution_id: str) -> dict:
    """Returns parsed constitution YAML. Raises ConstitutionNotFound if missing."""
    path = CONSTITUTIONS_DIR / f"{constitution_id}.yaml"
    if not path.exists():
        raise ConstitutionNotFound(f"constitution '{constitution_id}' not found at {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def available_constitutions() -> list[str]:
    """Returns IDs of all constitutions available in the registry."""
    if not CONSTITUTIONS_DIR.exists():
        return []
    return sorted(p.stem for p in CONSTITUTIONS_DIR.glob("*.yaml"))
