import importlib.util
import os
import unittest
from datetime import datetime, timedelta, timezone
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


class MorphoFetchTests(unittest.TestCase):
    def test_coingecko_history_uses_morpho_id_and_daily_usd_prices(self):
        response = Mock()
        response.json.return_value = {
            "prices": [
                [1790726400000, 1.25],
                [1790812800000, 1.5],
            ]
        }

        with patch.object(fetch_marcro.requests, "get", return_value=response) as request:
            values = fetch_marcro.fetch_coingecko_history("morpho", attempts=1)

        self.assertTrue(request.call_args.args[0].endswith("/coins/morpho/market_chart"))
        self.assertEqual(request.call_args.kwargs["params"]["vs_currency"], "usd")
        self.assertEqual(request.call_args.kwargs["params"]["interval"], "daily")
        self.assertEqual(values.tolist(), [1.25, 1.5])

    def test_forum_separates_new_and_refreshed_topics(self):
        now = datetime.now(timezone.utc)
        new_date = now.isoformat().replace("+00:00", "Z")
        old_date = (now - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        response = Mock()
        response.json.return_value = {
            "topic_list": {
                "topics": [
                    {"id": 1, "slug": "new", "title": "New", "created_at": new_date, "bumped_at": new_date, "posts_count": 2, "views": 10},
                    {"id": 2, "slug": "refreshed", "title": "Refreshed", "created_at": old_date, "bumped_at": new_date, "posts_count": 4, "views": 20},
                ]
            }
        }

        with patch.object(fetch_marcro.requests, "get", return_value=response):
            forum = fetch_marcro.fetch_morpho_forum()

        self.assertEqual([topic["id"] for topic in forum["new_topics"]], [1])
        self.assertEqual([topic["id"] for topic in forum["recently_refreshed_topics"]], [2])
        self.assertEqual(forum["recently_refreshed_topics"][0]["replies"], 3)

if __name__ == "__main__":
    unittest.main()
