import unittest
from unittest.mock import patch

from src.tools import cluster_snapshot


class ClusterSnapshotTest(unittest.TestCase):
    @patch("src.tools.get_pod_status", return_value="Pod 状态：Running")
    @patch("src.tools.query_loki_logs", return_value="Loki 日志：OK")
    @patch("src.tools.query_prometheus", return_value="Prometheus 查询结果：OK")
    def test_collects_metrics_logs_and_pods(
        self, prometheus, loki, pod_status
    ) -> None:
        result = cluster_snapshot("order-service")

        prometheus.assert_called_once_with()
        loki.assert_called_once_with(service="order-service")
        pod_status.assert_called_once_with(service="order-service")
        self.assertIn("集群环境快照（order-service）", result)
        self.assertIn("Prometheus 查询结果：OK", result)
        self.assertIn("Loki 日志：OK", result)
        self.assertIn("Pod 状态：Running", result)

    @patch("src.tools.get_pod_status", return_value="Pod 状态：Running")
    @patch("src.tools.query_loki_logs", return_value="Loki 日志：OK")
    @patch("src.tools.query_prometheus", side_effect=RuntimeError("metrics down"))
    def test_continues_when_one_source_fails(
        self, prometheus, loki, pod_status
    ) -> None:
        result = cluster_snapshot()

        self.assertIn("[FAIL] RuntimeError: metrics down", result)
        self.assertIn("Loki 日志：OK", result)
        self.assertIn("Pod 状态：Running", result)


if __name__ == "__main__":
    unittest.main()
