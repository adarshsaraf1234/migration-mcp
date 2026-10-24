"""Real-graph integration check through the existing JavaScript adapter."""

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from get_migration_context import get_migration_context


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
GRAPH_PATH = REPOSITORY_ROOT.parent / "legacy-demo-app" / "graphify-out" / "graph.json"


@unittest.skipUnless(GRAPH_PATH.is_file(), "native legacy-demo-app graph is not available")
class GraphContextIntegrationTest(unittest.TestCase):
    def test_native_find_by_id_context_has_both_verified_callers(self) -> None:
        result = get_migration_context(str(GRAPH_PATH), "legacy_utils_findbyid", max_depth=3)

        self.assertTrue(result["success"], result)
        self.assertEqual(result["target"]["source_file"], "legacy-utils.js")
        self.assertEqual(
            sorted(item["node"]["id"] for item in result["callers"]),
            ["order_service_createorder", "report_describeproduct"],
        )
        self.assertTrue(all(item["edge"]["relation"] == "calls" for item in result["callers"]))
        self.assertTrue(result["evidence"]["caller_edges"])


if __name__ == "__main__":
    unittest.main()
