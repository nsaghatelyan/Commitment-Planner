"""Billing scopes per agreement type, as used by Cost Management and benefit APIs."""

import re

AGREEMENT_TYPES = ("ea", "mca", "payg", "csp")

_PATTERNS = {
    "ea": re.compile(r"^/providers/Microsoft\.Billing/billingAccounts/[^/]+$", re.IGNORECASE),
    "mca": re.compile(
        r"^/providers/Microsoft\.Billing/billingAccounts/[^/]+/billingProfiles/[^/]+$",
        re.IGNORECASE,
    ),
    "payg": re.compile(r"^/subscriptions/[0-9a-f-]{36}$", re.IGNORECASE),
    "csp": re.compile(
        r"^/providers/Microsoft\.Billing/billingAccounts/[^/]+/customers/[^/]+$", re.IGNORECASE
    ),
}

_EXAMPLES = {
    "ea": "/providers/Microsoft.Billing/billingAccounts/<enrollment number>",
    "mca": "/providers/Microsoft.Billing/billingAccounts/<account id>/billingProfiles/<profile id>",
    "payg": "/subscriptions/<subscription id>",
    "csp": "/providers/Microsoft.Billing/billingAccounts/<partner account>/customers/<customer id>",
}


def validate_scope(agreement_type: str, scope: str) -> str:
    agreement_type = agreement_type.lower()
    if agreement_type not in _PATTERNS:
        raise ValueError(f"azure_agreement_type must be one of {AGREEMENT_TYPES}")
    scope = scope.rstrip("/")
    if not _PATTERNS[agreement_type].match(scope):
        raise ValueError(
            f"Billing scope for {agreement_type.upper()} should look like "
            f"{_EXAMPLES[agreement_type]}, got {scope!r}"
        )
    return scope


def scope_level_utilization(agreement_type: str) -> bool:
    """EA and MCA can read benefit utilization for all orders at the billing scope; PAYG and
    CSP read it per reservation / savings plan order."""
    return agreement_type.lower() in ("ea", "mca")
