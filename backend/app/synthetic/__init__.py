"""Synthetic tenants so later phases can run without a real cloud account."""

from app.synthetic.generator import PROFILES, SyntheticTenant, generate

__all__ = ["PROFILES", "SyntheticTenant", "generate"]
