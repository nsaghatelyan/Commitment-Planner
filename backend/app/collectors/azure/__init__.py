__all__ = ["AzureCollector"]


def __getattr__(name: str):
    # Lazy, so app.pricing.azure can import collectors.azure.http without a cycle.
    if name == "AzureCollector":
        from app.collectors.azure.collector import AzureCollector

        return AzureCollector
    raise AttributeError(name)
