import unittest

from src.alerts import (
    AlertValidationError,
    normalize_alert,
    normalize_alert_payload,
)


class NormalizeAlertTest(unittest.TestCase):
    def test_alertmanager_payload_is_normalized(self) -> None:
        payload = {
            "status": "firing",
            "commonLabels": {"region": "cn"},
            "commonAnnotations": {"source": "prometheus"},
            "alerts": [
                {
                    "status": "firing",
                    "fingerprint": "abc123",
                    "labels": {
                        "alertname": "HighCPU",
                        "service": "order-service",
                        "severity": "critical",
                    },
                    "annotations": {
                        "summary": "CPU usage is high",
                        "description": "CPU usage > 90% for 5 minutes",
                    },
                },
                {
                    "status": "resolved",
                    "fingerprint": "def456",
                    "labels": {"alertname": "HighLatency"},
                    "annotations": {},
                },
            ],
        }

        alerts = normalize_alert_payload(payload)

        self.assertEqual(len(alerts), 2)
        self.assertEqual(alerts[0]["alert_id"], "abc123")
        self.assertEqual(alerts[0]["alert_name"], "HighCPU")
        self.assertEqual(alerts[0]["service"], "order-service")
        self.assertEqual(
            alerts[0]["message"], "CPU usage > 90% for 5 minutes"
        )
        self.assertEqual(alerts[0]["labels"]["region"], "cn")
        self.assertEqual(alerts[0]["status"], "firing")
        self.assertEqual(alerts[1]["status"], "resolved")

    def test_simplified_alert_is_supported(self) -> None:
        alert = normalize_alert(
            {
                "alert_name": "PodRestart",
                "service": "checkout",
                "message": "Pod restarted repeatedly",
                "severity": "warning",
            }
        )

        self.assertTrue(alert["alert_id"].startswith("alert-"))
        self.assertEqual(alert["alert_name"], "PodRestart")
        self.assertEqual(alert["service"], "checkout")
        self.assertEqual(alert["status"], "firing")

    def test_empty_alert_list_is_rejected(self) -> None:
        with self.assertRaises(AlertValidationError):
            normalize_alert_payload({"alerts": []})

    def test_webhook_status_is_used_when_item_status_is_missing(self) -> None:
        alerts = normalize_alert_payload(
            {
                "status": "resolved",
                "alerts": [{"fingerprint": "resolved-alert"}],
            }
        )

        self.assertEqual(alerts[0]["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
