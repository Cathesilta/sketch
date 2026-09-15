import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "script" / "fetch_marcro.py"
SPEC = importlib.util.spec_from_file_location("fetch_marcro", SCRIPT_PATH)
fetch_marcro = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch_marcro)


class FredParsingTests(unittest.TestCase):
    def test_parse_data_page_reads_table_and_embedded_rows(self):
        html = """
        <table id="data-table-observations">
          <tr><th scope="row" class="pe-5">2026-09-09</th><td>4.83</td></tr>
        </table>
        <div id="extra-rows">
          #2026-09-10| 4.95
          #2026-09-11| 4.96
          #2026-09-12|    .
        </div>
        """

        values = fetch_marcro.parse_fred_data_page(html, "DGS10")

        self.assertEqual(
            values.index.strftime("%Y-%m-%d").tolist(),
            ["2026-09-09", "2026-09-10", "2026-09-11"],
        )
        self.assertEqual(values.tolist(), [4.83, 4.95, 4.96])

    def test_parse_data_page_rejects_page_without_observations(self):
        with self.assertRaisesRegex(ValueError, "no observations"):
            fetch_marcro.parse_fred_data_page("<html></html>", "DGS10")

    def test_fetch_uses_public_alfred_csv_without_key(self):
        response = Mock()
        response.text = "observation_date,DGS10_20260915\n2026-09-11,4.96\n"

        with patch.object(fetch_marcro.requests, "get", return_value=response) as request:
            values = fetch_marcro.fetch_fred("DGS10", attempts=1)

        self.assertIn("alfred.stlouisfed.org/graph/alfredgraph.csv", request.call_args.args[0])
        self.assertNotIn("api_key", request.call_args.args[0])
        self.assertEqual(values.iloc[-1], 4.96)

    def test_fetch_falls_back_to_graph_csv(self):
        alfred = Mock()
        alfred.text = "temporarily incomplete"
        page = Mock()
        page.text = "<html>temporarily incomplete</html>"
        csv = Mock()
        csv.text = "DATE,DGS10\n2026-09-11,4.96\n"

        with patch.object(
            fetch_marcro.requests, "get", side_effect=[alfred, page, csv]
        ) as request:
            values = fetch_marcro.fetch_fred("DGS10", attempts=1)

        self.assertEqual(request.call_count, 3)
        self.assertIn("fredgraph.csv", request.call_args.args[0])
        self.assertEqual(values.iloc[-1], 4.96)

    def test_strict_refresh_rejects_cached_fred_series(self):
        payload = {
            "series": [
                {
                    "source": "FRED",
                    "symbol": "DGS10",
                    "status": "cached",
                    "error": "timeout",
                },
                {
                    "source": "Yahoo",
                    "symbol": "^VIX",
                    "status": "fresh",
                    "error": None,
                },
            ]
        }

        with self.assertRaisesRegex(RuntimeError, "DGS10: timeout"):
            fetch_marcro.require_fresh_fred(payload)

    def test_strict_refresh_accepts_fresh_fred_series(self):
        payload = {
            "series": [
                {
                    "source": "FRED",
                    "symbol": "DGS10",
                    "status": "fresh",
                    "error": None,
                },
                {
                    "source": "Yahoo",
                    "symbol": "^VIX",
                    "status": "cached",
                    "error": "timeout",
                },
            ]
        }

        fetch_marcro.require_fresh_fred(payload)


if __name__ == "__main__":
    unittest.main()
