import tempfile
import unittest
from pathlib import Path

from src.approval_audit import ApprovalAuditStore


class ApprovalAuditStoreTest(unittest.TestCase):
    def test_records_who_when_and_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "approvals.sqlite")
            store = ApprovalAuditStore(db_path)
            record = store.record(
                thread_id="web-123",
                alert_id="alert-456",
                action="restart_pod",
                args={"service": "order-service"},
                decision="approved",
                approved=True,
                actor="alice",
                actor_source="web_body",
                source="web",
                trace_id="trace-123",
                skills=[
                    {
                        "id": "remediate-service",
                        "version": "1.0.0",
                        "content_hash": "abc123",
                    }
                ],
            )
            records = store.list(thread_id="web-123")

        self.assertTrue(record["event_id"])
        self.assertTrue(record["decided_at"].endswith("Z"))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["actor"], "alice")
        self.assertEqual(records[0]["trace_id"], "trace-123")
        self.assertEqual(records[0]["action"], "restart_pod")
        self.assertEqual(records[0]["args"], {"service": "order-service"})
        self.assertEqual(records[0]["skills"][0]["id"], "remediate-service")
        self.assertEqual(records[0]["skills"][0]["version"], "1.0.0")
        self.assertTrue(records[0]["approved"])

    def test_records_rejection_without_marking_approved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ApprovalAuditStore(str(Path(temp_dir) / "approvals.sqlite"))
            store.record(
                action="scale_deployment",
                args={"service": "checkout", "replicas": 3},
                decision="rejected",
                approved=False,
                actor="bob",
                actor_source="web_header",
                source="web",
            )
            records = store.list()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["decision"], "rejected")
        self.assertFalse(records[0]["approved"])

    def test_rejects_empty_actor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ApprovalAuditStore(str(Path(temp_dir) / "approvals.sqlite"))
            with self.assertRaises(ValueError):
                store.record(
                    action="restart_pod",
                    args={},
                    decision="approved",
                    approved=True,
                    actor=" ",
                    actor_source="web_body",
                    source="web",
                )


if __name__ == "__main__":
    unittest.main()
