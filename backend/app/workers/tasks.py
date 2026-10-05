"""Background jobs. Each one is a stub until its phase lands."""

from typing import Any


async def collect_usage(ctx: dict[str, Any], cloud_connection_id: str) -> None:
    """Pull usage and commitments for one connection and write Parquet. Records a collection_run."""
    raise NotImplementedError


async def run_analysis(ctx: dict[str, Any], tenant_id: str) -> None:
    """Run the engine over stored usage and write recommendations. Records an analysis_run."""
    raise NotImplementedError


async def refresh_prices(ctx: dict[str, Any]) -> None:
    """Refresh the price table from AWS and Azure public pricing."""
    raise NotImplementedError
