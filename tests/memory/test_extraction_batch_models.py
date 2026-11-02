"""契约 §2 边界类型：ExtractionRequest / BatchReceipt。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from nanobot.memory.models import BatchReceipt, ExtractionRequest


class TestExtractionRequest:
    def test_fields_and_defaults(self) -> None:
        request = ExtractionRequest(
            workspace_id="ws",
            session_key="cli:1",
            generation="gen-1",
            through_seq=7,
            reason="idle",
        )
        assert request.workspace_id == "ws"
        assert request.session_key == "cli:1"
        assert request.generation == "gen-1"
        assert request.through_seq == 7
        assert request.reason == "idle"
        assert request.flush is False

    def test_is_frozen(self) -> None:
        request = ExtractionRequest("ws", "cli:1", "gen-1", 1, "idle", flush=True)
        with pytest.raises(FrozenInstanceError):
            request.through_seq = 2  # type: ignore[misc]


class TestBatchReceipt:
    def test_defaults(self) -> None:
        receipt = BatchReceipt(batch_id="b1", status="pending", through_seq=4)
        assert receipt.memory_ids == ()
        assert receipt.episode_ids == ()
        assert receipt.error_code is None

    def test_completed_receipt_carries_ids(self) -> None:
        receipt = BatchReceipt(
            batch_id="b1",
            status="completed",
            through_seq=4,
            memory_ids=("m1",),
            episode_ids=("e1",),
        )
        assert receipt.memory_ids == ("m1",)
        assert receipt.episode_ids == ("e1",)

    def test_failed_receipt_keeps_structured_error(self) -> None:
        receipt = BatchReceipt(
            batch_id="b1",
            status="failed",
            through_seq=4,
            error_code="BATCH_PAYLOAD_MISMATCH",
        )
        assert receipt.error_code == "BATCH_PAYLOAD_MISMATCH"

    def test_is_frozen(self) -> None:
        receipt = BatchReceipt(batch_id="b1", status="pending", through_seq=1)
        with pytest.raises(FrozenInstanceError):
            receipt.status = "completed"  # type: ignore[misc]
