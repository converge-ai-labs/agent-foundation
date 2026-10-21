"""Bounded Prometheus HTTP queries. Authorization and source resolution precede external I/O."""

from dataclasses import dataclass, field
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

import anyio
import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, model_validator

from a13n_service.infra.errors import ServiceError, invalid, rate_limited
from a13n_service.infra.outbound import open_http


def endpoint(value: str) -> str:
    parsed = urlsplit(value)
    # urlsplit defers malformed and out-of-range port validation until access.
    _ = parsed.port
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Use an HTTP(S) endpoint without userinfo, query or fragment")
    return value.rstrip("/")


Endpoint = Annotated[str, Field(min_length=1, max_length=2048), AfterValidator(endpoint)]
Timestamp = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Expression = Annotated[str, Field(min_length=1, max_length=16384)]


class InstantQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["instant"] = "instant"
    expression: Expression
    time: Timestamp | None = None


class Range(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    start: Timestamp
    end: Timestamp
    step: float = Field(ge=1, le=86400, allow_inf_nan=False)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.end < self.start:
            raise ValueError("end must not precede start")
        if self.end - self.start > 90 * 86400:
            raise ValueError("query range must not exceed 90 days")
        if (self.end - self.start) / self.step >= 11000:
            raise ValueError("query must not exceed 11000 points per series")
        return self


class RangeQuery(Range):
    kind: Literal["range"] = "range"
    expression: Expression


type QueryRequest = Annotated[InstantQuery | RangeQuery, Field(discriminator="kind")]


class QueryData(BaseModel):
    resultType: Literal["matrix", "vector", "scalar", "string"]
    # Preserve Prometheus values, including non-finite sample strings and native histograms.
    result: list[JsonValue]


class QueryResult(BaseModel):
    status: Literal["success"]
    data: QueryData
    warnings: list[str] = Field(default_factory=list)
    infos: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class Destination:
    endpoint: str
    headers: dict[str, str] = field(default_factory=dict, repr=False)


class Queries:
    """One process's concurrency budget, independent of collection capacity and without a waiting queue."""

    def __init__(self, *, concurrency: int = 8, timeout: float = 5, max_bytes: int = 4194304):
        self.slots = anyio.CapacityLimiter(concurrency)
        self.timeout = timeout
        self.max_bytes = max_bytes

    async def execute(self, policy: EndpointPolicy, destination: Destination, query: QueryRequest) -> QueryResult:
        try:
            self.slots.acquire_nowait()
        except anyio.WouldBlock:
            raise rate_limited("Metrics query capacity is exhausted", 1) from None
        try:
            with anyio.fail_after(self.timeout):
                return await self._execute(policy, destination, query)
        except (TimeoutError, httpx2.TimeoutException):
            raise ServiceError("request_timeout", "Metrics backend query timed out") from None
        except httpx2.HTTPError:
            raise ServiceError("unavailable", "Metrics backend request failed") from None
        except ValueError:
            # Policy errors and malformed backend bodies can contain URLs or upstream credentials.
            raise ServiceError(
                "unavailable", "Metrics backend returned an invalid response or is not permitted"
            ) from None
        finally:
            self.slots.release()

    async def _execute(self, policy: EndpointPolicy, destination: Destination, query: QueryRequest) -> QueryResult:
        parameters = {"query": query.expression, "timeout": f"{self.timeout:g}s", "limit": "200"}
        if isinstance(query, RangeQuery):
            path = "query_range"
            parameters.update(start=str(query.start), end=str(query.end), step=str(query.step))
        else:
            path = "query"
            if query.time is not None:
                parameters["time"] = str(query.time)
        async with open_http(policy, timeout=self.timeout, max_bytes=self.max_bytes) as client:
            response = await client.post(
                f"{destination.endpoint}/api/v1/{path}",
                data=parameters,
                headers={**destination.headers, "content-type": "application/x-www-form-urlencoded"},
            )
            if response.status_code in {400, 422}:
                raise invalid("expression", "the metrics backend rejected this query")
            if response.status_code == 429:
                raise rate_limited("Metrics backend query capacity is exhausted", 1)
            if response.status_code == 503:
                raise ServiceError("unavailable", "Metrics backend could not execute the query")
            response.raise_for_status()
            result = QueryResult.model_validate_json(response.content)
            if result.data.resultType in {"matrix", "vector"} and len(result.data.result) > 200:
                raise ServiceError("payload_too_large", "Metrics result exceeds the series limit", {"limit": 200})
            return result
