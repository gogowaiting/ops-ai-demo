import tempfile
import unittest
from pathlib import Path

from src.alert_jobs import AlertJobStore


class AlertJobStoreTest(unittest.TestCase):
    def test_alert_job_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AlertJobStore(str(Path(temp_dir) / "jobs.sqlite"))
            alert = {"alert_id": "alert-1", "message": "CPU high"}
            job = store.enqueue(alert, trace_id="trace-1", job_id="job-1")

            self.assertEqual(job["status"], "queued")
            self.assertTrue(store.mark_started("job-1"))
            self.assertFalse(store.mark_started("job-1"))
            self.assertTrue(
                store.complete("job-1", steps=["step-1", "step-2"])
            )

            loaded = store.get("job-1")
            self.assertEqual(loaded["status"], "completed")
            self.assertEqual(loaded["steps"], ["step-1", "step-2"])
            self.assertEqual(loaded["alert"]["alert_id"], "alert-1")

    def test_failed_job_records_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AlertJobStore(str(Path(temp_dir) / "jobs.sqlite"))
            store.enqueue({"alert_id": "alert-2"}, job_id="job-2")
            store.mark_started("job-2")
            self.assertTrue(store.fail("job-2", "boom"))

            loaded = store.get("job-2")
            self.assertEqual(loaded["status"], "failed")
            self.assertEqual(loaded["error"], "boom")


if __name__ == "__main__":
    unittest.main()
