"""OpenTelemetry tracing configuration with JSONL file export."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from opentelemetry.sdk.trace import ReadableSpan

logger = logging.getLogger(__name__)


class JSONLFileExporter(SpanExporter):
    """Exports spans as JSONL to a file (<app_name>-otel.log)."""

    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        self._path = path

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Write spans as JSON lines to the log file."""
        with self._path.open("a", encoding="utf-8") as fh:
            for span in spans:
                record: dict[str, Any] = {
                    "name": span.name,
                    "trace_id": f"{span.context.trace_id:032x}" if span.context else "",
                    "span_id": f"{span.context.span_id:016x}" if span.context else "",
                    "parent_span_id": (f"{span.parent.span_id:016x}" if span.parent else None),
                    "start_time": span.start_time,
                    "end_time": span.end_time,
                    "status": span.status.status_code.name if span.status else "UNSET",
                    "attributes": dict(span.attributes) if span.attributes else {},
                }
                if span.events:
                    record["events"] = [
                        {
                            "name": event.name,
                            "timestamp": event.timestamp,
                            "attributes": dict(event.attributes) if event.attributes else {},
                        }
                        for event in span.events
                    ]
                fh.write(json.dumps(record, default=str) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        """No resources to release."""


def configure_tracing(
    app_name: str = "app",
    *,
    log_dir: Path | None = None,
) -> TracerProvider:
    """Configure OpenTelemetry tracing with JSONL file export.

    Creates a tracer provider that writes spans to <app_name>-otel.log.

    Args:
        app_name: Application name, used for service name and log file (<app_name>-otel.log)
        log_dir: Directory for the JSONL log file (default: current working directory)
    """
    otel_path = (log_dir or Path.cwd()) / f"{app_name}-otel.log"

    resource = Resource.create({"service.name": app_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(JSONLFileExporter(otel_path)))
    trace.set_tracer_provider(provider)

    logger.info("OpenTelemetry tracing configured, writing to %s", otel_path)
    return provider


@contextmanager
def trace_span(
    name: str,
    attributes: dict[str, Any] | None = None,
) -> Iterator[trace.Span]:
    """Context manager for creating traced spans.

    Follows the category.operation naming convention
    (e.g., 'api.fetch_users', 'db.query', 'llm.call').

    Args:
        name: Span name in category.operation format
        attributes: Optional span attributes
    """
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(name, attributes=attributes) as span:
        try:
            yield span
        except Exception as exc:
            span.set_status(trace.StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise
