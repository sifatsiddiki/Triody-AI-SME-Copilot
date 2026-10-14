"""Country settings. Everything that ties Triody to one country lives in the catalogue file.

A catalogue file (catalog.json for the UK, catalog_cn.json for mainland China) has a "region" block:
currency symbol, the name of the tax authority, words that mark a commitment as a tax bill, and the
largest amount the agent may ever submit. A shop picks its catalogue in business_profile.json
("catalog"). Moving Triody to another country means writing one new catalogue file and nothing else.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_REGION = {"code": "UK", "currency": "GBP", "symbol": "£", "tax_authority": "HMRC",
                  "tax_keywords": ["HMRC"], "max_application": 50000.0}


def money(x: float, symbol: str = "£", signed: bool = True) -> str:
    if signed and x < 0:
        return f"-{symbol}{abs(x):,.0f}"
    return f"{symbol}{abs(x):,.0f}"


def catalog_path(name: str = None) -> Path:
    """Only files next to this module can be named, so a profile cannot point anywhere else."""
    name = Path(name or "catalog.json").name
    return HERE / name


def load_region(name: str = None) -> dict:
    data = json.loads(catalog_path(name).read_text(encoding="utf-8"))
    return {**DEFAULT_REGION, **data.get("region", {})}


def is_tax(commitment: dict, region: dict) -> bool:
    if commitment.get("kind") == "tax":
        return True
    label = commitment.get("label", "")
    return any(k.lower() in label.lower() for k in region.get("tax_keywords", ()))
