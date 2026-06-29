"""Tests for the OpenTelemetry JSONL tracing helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_fs.tracing import configure_tracing, trace_span


def test_configure_writes_jsonl(tmp_path: Path) -> None:
    # Use the returned provider directly: OTel's global set_tracer_provider only
    # honors the first call per process, so asserting on the global would be flaky.
    provider = configure_tracing(app_name="test-fs", log_dir=tmp_path)
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("test.op"):
        pass
    log = tmp_path / "test-fs-otel.log"
    assert log.exists()
    assert "test.op" in log.read_text(encoding="utf-8")


def test_span_records_exception() -> None:
    configure_tracing(app_name="test-fs-err")
    with pytest.raises(ValueError, match="boom"), trace_span("test.err", {"k": "v"}):
        raise ValueError("boom")
