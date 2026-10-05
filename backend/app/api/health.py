from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness check. Does not touch Postgres or Redis."""
    return {"status": "ok"}
