"""One process meter provider shared by HTTP and Harness, scraped by Prometheus."""

from dataclasses import dataclass

from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.metrics import Counter, Histogram
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

PROMETHEUS_CONTENT_TYPE = CONTENT_TYPE_LATEST


@dataclass(slots=True)
class MetricsRuntime:
    provider: MeterProvider
    registry: CollectorRegistry
    requests: Counter
    duration: Histogram

    def render(self) -> bytes:
        return generate_latest(self.registry)

    def shutdown(self) -> None:
        self.provider.shutdown()


def build_metrics_runtime(*, resource: Resource) -> MetricsRuntime:
    registry = CollectorRegistry()
    reader = PrometheusMetricReader(registry=registry)
    provider = MeterProvider(
        resource=resource,
        metric_readers=[reader],
        shutdown_on_exit=False,
        views=[
            View(
                instrument_name="a13n.service.http.duration",
                aggregation=ExplicitBucketHistogramAggregation(
                    boundaries=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
                ),
            )
        ],
    )
    meter = provider.get_meter("a13n-a13n-service")
    return MetricsRuntime(
        provider,
        registry,
        meter.create_counter("a13n.service.http.requests", unit="{request}", description="Finished HTTP requests"),
        meter.create_histogram(
            "a13n.service.http.duration", unit="s", description="HTTP duration through response completion"
        ),
    )
