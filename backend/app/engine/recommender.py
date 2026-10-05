from pathlib import Path


def recommend(tenant_id: str, usage_dir: Path) -> list[dict]:
    """Return savings plan / reservation recommendations for a tenant."""
    raise NotImplementedError
