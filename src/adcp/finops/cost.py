"""Token cost model: usage x provider price table."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

from adcp.types import Usage

PRICES_PATH = Path(__file__).with_name("prices.yaml")


class ModelPrice(BaseModel):
    provider: str
    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0


class PriceTable(BaseModel):
    models: dict[str, ModelPrice]

    def get(self, model_id: str) -> ModelPrice:
        if model_id in self.models:
            return self.models[model_id]
        for key, price in self.models.items():  # tolerate provider prefixes / suffixes
            if key in model_id or model_id in key:
                return price
        raise KeyError(f"no price for model {model_id!r}")


@lru_cache(maxsize=4)
def load_prices(path: str | None = None) -> PriceTable:
    data = yaml.safe_load(Path(path or PRICES_PATH).read_text())
    return PriceTable(models={k: ModelPrice(**v) for k, v in data["models"].items()})


def cost_usd(usage: Usage, model_id: str, table: PriceTable | None = None) -> float:
    p = (table or load_prices()).get(model_id)
    return (
        usage.input_tokens * p.input
        + usage.output_tokens * p.output
        + usage.cache_read_tokens * p.cache_read
        + usage.cache_write_tokens * p.cache_write
    ) / 1_000_000
