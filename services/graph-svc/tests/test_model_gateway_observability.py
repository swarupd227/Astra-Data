"""S12.2.1: Model gateway observability tests — latency, context hash, prompt versioning."""

from __future__ import annotations

import pytest

from astra_graph.gateway import (
    PROMPT_TEMPLATE_VERSION,
    RawModelResponse,
    _compute_context_hash,
)


class TestContextHash:
    """Context hash computation for payload separation from template."""

    def test_context_hash_differs_from_payload_changes(self):
        """Different payloads produce different context hashes."""
        payload1 = {"output_schema": {"field": "string"}, "constraints": ["a"]}
        payload2 = {"output_schema": {"field": "string"}, "constraints": ["b"]}

        hash1 = _compute_context_hash(payload1)
        hash2 = _compute_context_hash(payload2)

        assert hash1 != hash2

    def test_context_hash_stable_for_same_payload(self):
        """Same payload produces same hash (deterministic JSON)."""
        payload = {"field": "value", "nested": {"a": 1, "b": 2}}

        hash1 = _compute_context_hash(payload)
        hash2 = _compute_context_hash(payload)

        assert hash1 == hash2

    def test_context_hash_ignores_key_order(self):
        """Canonical JSON ensures key order doesn't affect hash."""
        payload1 = {"a": 1, "b": 2}
        payload2 = {"b": 2, "a": 1}

        assert _compute_context_hash(payload1) == _compute_context_hash(payload2)


class TestRawModelResponse:
    """Raw model response with S12.2.1 fields."""

    def test_response_includes_observability_fields(self):
        """Response captures latency, context hash, and template version."""
        response = RawModelResponse(
            raw={"result": "test"},
            gateway_request_id="gwreq_test123",
            provider="anthropic",
            model="claude-sonnet-5",
            prompt_hash="sha256:abc123",
            context_hash="sha256:def456",
            temperature=0.0,
            tokens_in=100,
            tokens_out=50,
            latency_ms=245.3,
            prompt_template_version="dev",
        )

        assert response.context_hash == "sha256:def456"
        assert response.latency_ms == 245.3
        assert response.prompt_template_version == "dev"

    def test_prompt_template_version_constant_exists(self):
        """Prompt template version constant is defined (Git SHA or 'dev')."""
        assert PROMPT_TEMPLATE_VERSION is not None
        assert isinstance(PROMPT_TEMPLATE_VERSION, str)
        assert len(PROMPT_TEMPLATE_VERSION) > 0
