"""Small Azure REST client: ARM auth, nextLink paging and Cost Management throttling retries."""

import time
from collections.abc import Callable, Iterator
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

ARM = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"
RETRY_STATUS = {429, 500, 502, 503, 504}
# Cost Management returns these instead of (or alongside) Retry-After.
RETRY_HEADERS = (
    "retry-after",
    "x-ms-ratelimit-microsoft.costmanagement-qpu-retry-after",
    "x-ms-ratelimit-microsoft.costmanagement-entity-retry-after",
    "x-ms-ratelimit-microsoft.costmanagement-tenant-retry-after",
    "x-ms-ratelimit-microsoft.consumption-retry-after",
)


class TokenCredential(Protocol):
    def get_token(self, *scopes: str) -> Any: ...


class AzureHttpError(Exception):
    def __init__(self, status: int, url: str, body: str) -> None:
        super().__init__(f"{status} from {url}: {body[:500]}")
        self.status = status
        self.body = body


class AzureHttp:
    def __init__(
        self,
        credential: TokenCredential | None,
        *,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 6,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.credential = credential
        self.client = httpx.Client(transport=transport, timeout=120)
        self.max_retries = max_retries
        self.sleep = sleep

    def _headers(self, url: str) -> dict[str, str]:
        if urlparse(url).netloc == urlparse(ARM).netloc and self.credential is not None:
            return {"Authorization": f"Bearer {self.credential.get_token(ARM_SCOPE).token}"}
        return {}

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> dict[str, Any]:
        if url.startswith("/"):
            url = ARM + url
        for attempt in range(self.max_retries + 1):
            resp = self.client.request(
                method, url, params=params, json=json, headers=self._headers(url)
            )
            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                self.sleep(_retry_after(resp, attempt))
                continue
            if resp.status_code >= 400:
                raise AzureHttpError(resp.status_code, url, resp.text)
            return resp.json() if resp.content else {}
        raise AssertionError("unreachable")

    def paged(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        call: Callable[..., Any] | None = None,
        api: str = "azure:GET",
    ) -> Iterator[dict[str, Any]]:
        """Yield `value` items across nextLink pages (GET list endpoints)."""
        next_url: str | None = url
        next_params = params
        while next_url:
            if call:
                page = call(api, self.request, method="GET", url=next_url, params=next_params)
            else:
                page = self.request("GET", next_url, params=next_params)
            yield from page.get("value", [])
            next_url = page.get("nextLink")
            next_params = None  # nextLink already carries the query string


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    for header in RETRY_HEADERS:
        value = resp.headers.get(header)
        if value:
            try:
                return max(float(value), 1.0)
            except ValueError:
                continue
    return min(2**attempt, 60)
