import unittest

from src.tool_registry import ToolDefinition, ToolRegistrationError, ToolRegistry


class ToolRegistryTest(unittest.TestCase):
    def test_registers_connector_and_dispatches_read_tool(self) -> None:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition("search_cases", "Search cases", lambda query: f"case:{query}")
        )

        self.assertEqual(
            registry.dispatch("search_cases", {"query": "oom"}),
            "case:oom",
        )
        self.assertEqual(registry.names(), {"search_cases"})

    def test_write_tool_approval_is_enforced_by_registry(self) -> None:
        received = {}

        def execute(service: str, approved: bool = False) -> str:
            received["approved"] = approved
            return "done"

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                "restart_service",
                "Restart service",
                execute,
                risk="high",
                write_op=True,
            )
        )

        result = registry.dispatch(
            "restart_service",
            {"service": "order", "approved": True},
            approved=False,
        )

        self.assertEqual(result, "done")
        self.assertFalse(received["approved"])
        self.assertEqual(registry.write_tools(), {"restart_service"})

    def test_rejects_duplicate_tool(self) -> None:
        registry = ToolRegistry()
        definition = ToolDefinition("duplicate", "Duplicate", lambda: "ok")
        registry.register(definition)
        with self.assertRaises(ToolRegistrationError):
            registry.register(definition)


if __name__ == "__main__":
    unittest.main()
