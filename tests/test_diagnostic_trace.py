import tempfile
import unittest
from pathlib import Path

from src.diagnostic_trace import DiagnosticTraceStore


class DiagnosticTraceStoreTest(unittest.TestCase):
    def test_trace_events_and_finish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DiagnosticTraceStore(str(Path(temp_dir) / "traces.sqlite"))
            store.start(
                trace_id="trace-1",
                source="web_chat",
                input_text="check cluster",
                thread_id="thread-1",
            )
            store.record(trace_id="trace-1", node="llm", content="ACTION")
            store.record(
                trace_id="trace-1",
                node="tools",
                content="metrics",
                metadata={"source": "test"},
            )
            store.finish("trace-1", "completed")

            trace = store.get("trace-1")
            self.assertEqual(trace["status"], "completed")
            self.assertEqual(len(trace["events"]), 2)
            self.assertEqual(trace["events"][0]["content"], "ACTION")
            self.assertEqual(
                trace["events"][1]["metadata"]["source"],
                "test",
            )


if __name__ == "__main__":
    unittest.main()
