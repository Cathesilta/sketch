import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "script" / "fetch_marcro.py"
SPEC = importlib.util.spec_from_file_location("fetch_marcro", SCRIPT_PATH)
fetch_marcro = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch_marcro)


class FredFetchTests(unittest.TestCase):
    def test_api_key_uses_authenticated_observations_endpoint(self):
        response = Mock()
        response.json.return_value = {
            "observations": [
                {"date": "2026-09-17", "value": "."},
                {"date": "2026-09-18", "value": "5.01"},
            ]
        }

        with patch.dict(os.environ, {"FRED_API_KEY": "test-secret"}), patch.object(
            fetch_marcro.requests, "get", return_value=response
        ) as request:
            values = fetch_marcro.fetch_fred("DGS10")

        self.assertEqual(request.call_args.args[0], fetch_marcro.FRED_API_URL)
        self.assertEqual(request.call_args.kwargs["params"]["api_key"], "test-secret")
        self.assertEqual(values.index.strftime("%Y-%m-%d").tolist(), ["2026-09-18"])
        self.assertEqual(values.iloc[-1], 5.01)

    def test_without_key_uses_reference_csv_method(self):
        frame = fetch_marcro.pd.DataFrame(
            {"Date": ["2026-09-18"], "DGS10": ["5.01"]}
        )

        with patch.dict(os.environ, {}, clear=True), patch.object(
            fetch_marcro, "fetch_fred_csv", return_value=frame
        ) as fetch_csv:
            values = fetch_marcro.fetch_fred("DGS10")

        fetch_csv.assert_called_once()
        self.assertEqual(values.iloc[-1], 5.01)


if __name__ == "__main__":
    unittest.main()
