import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import TokenProfileSelector as selector
from strategy.pic_filter import PicCheck, should_block_near_peak


class FlatTokenHoldTests(unittest.TestCase):
    def test_every_token_is_held_before_absolute_minimum_age(self):
        with patch.object(selector, "SELECTOR_FLAT_MIN_HOLD_MINUTES", 5.0):
            self.assertAlmostEqual(
                selector._minimum_hold_remaining_minutes(2.25),
                2.75,
            )

    def test_token_can_switch_after_absolute_minimum_age(self):
        with patch.object(selector, "SELECTOR_FLAT_MIN_HOLD_MINUTES", 5.0):
            self.assertEqual(
                selector._minimum_hold_remaining_minutes(5.0),
                0.0,
            )


class CandidateWindowTests(unittest.TestCase):
    def test_change_window_uses_only_requested_recent_candles(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return [
                    [0, "50", "51", "49", "50", "0"],
                    [0, "90", "91", "89", "90", "0"],
                    [0, "100", "101", "99", "100", "0"],
                    [0, "100", "102", "99", "101", "0"],
                ]

        with patch.object(selector._SESSION, "get", return_value=Response()):
            self.assertAlmostEqual(
                selector.change_window_pct("BTCUSDC", minutes=2),
                1.0,
            )

    def test_change_window_ignores_future_kraken_placeholder_candle(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "error": [],
                    "result": {
                        "BTCUSDC": [
                            [60, "100", "101", "99", "100", "0"],
                            [120, "100", "103", "99", "102", "0"],
                            [180, "102", "102", "102", "102", "0"],
                        ],
                        "last": 120,
                    },
                }

        with (
            patch.object(selector, "_PAIR_META", {"BTCUSDC": {"pair_id": "BTCUSDC"}}),
            patch.object(selector._SESSION, "get", return_value=Response()),
        ):
            self.assertAlmostEqual(selector.change_window_pct("BTCUSDC", minutes=2), 2.0)

    def test_rejects_micro_move(self):
        with (
            patch.object(selector, "SELECTOR_MIN_WINDOW_PCT", 0.15),
            patch.object(selector, "SELECTOR_MAX_WINDOW_PCT", 1.8),
            patch.object(selector, "SELECTOR_MIN_MOVE_TO_SPREAD", 2.0),
        ):
            self.assertFalse(selector._candidate_window_is_eligible(0.09, 0.01))

    def test_rejects_move_too_small_for_spread(self):
        with (
            patch.object(selector, "SELECTOR_MIN_WINDOW_PCT", 0.15),
            patch.object(selector, "SELECTOR_MAX_WINDOW_PCT", 1.8),
            patch.object(selector, "SELECTOR_MIN_MOVE_TO_SPREAD", 2.0),
        ):
            self.assertFalse(selector._candidate_window_is_eligible(0.18, 0.10))

    def test_accepts_directional_move_with_edge(self):
        with (
            patch.object(selector, "SELECTOR_MIN_WINDOW_PCT", 0.15),
            patch.object(selector, "SELECTOR_MAX_WINDOW_PCT", 1.8),
            patch.object(selector, "SELECTOR_MIN_MOVE_TO_SPREAD", 2.0),
        ):
            self.assertTrue(selector._candidate_window_is_eligible(0.30, 0.05))

class KrakenTickerTests(unittest.TestCase):
    def test_market_stats_keeps_utc_session_change_out_of_rolling_24h_gate(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "error": [],
                    "result": {
                        "BTCUSDC": {
                            "c": ["110"],
                            "v": ["1", "2"],
                            "p": ["100", "105"],
                            "t": [1, 2],
                            "o": "100",
                        }
                    },
                }

        with (
            patch.object(selector, "_PAIR_META", {"BTCUSDC": {"pair_id": "BTCUSDC"}}),
            patch.object(selector._SESSION, "get", return_value=Response()),
        ):
            stats = selector.get_market_stats_map()

        self.assertIsNone(stats["BTCUSDC"]["change_pct_24h"])
        self.assertEqual(stats["BTCUSDC"]["session_change_pct"], 10.0)


class Rolling24hTests(unittest.TestCase):
    def test_rolling_24h_uses_288_five_minute_bars(self):
        bars = [
            [index * 300, "100", "101", "99", "100", "0"]
            for index in range(287)
        ]
        bars.append([287 * 300, "100", "105", "99", "110", "0"])
        with patch.object(selector, "_recent_ohlc_bars_for_interval", return_value=bars) as fetch:
            change = selector.rolling_24h_change_pct("BTCUSDC")

        fetch.assert_called_once_with("BTCUSDC", interval=5)
        self.assertEqual(change, 10.0)

    def test_missing_rolling_history_is_not_treated_as_zero_change(self):
        with patch.object(selector, "_recent_ohlc_bars_for_interval", return_value=[]):
            self.assertIsNone(selector.rolling_24h_change_pct("BTCUSDC"))


class SelectorObservabilityTests(unittest.TestCase):
    def test_rejected_candidates_include_the_first_blocking_gate(self):
        markets = {
            "BTCUSDC": {
                "last_price": 100.0,
                "quote_volume_24h": 9_000_000.0,
                "trade_count_24h": 10_000,
                "change_pct_24h": 1.0,
            },
            "XMRUSDC": {
                "last_price": 100.0,
                "quote_volume_24h": 9_000_000.0,
                "trade_count_24h": 10_000,
                "change_pct_24h": 1.0,
            },
        }
        eligible, counts, rejected = selector._tradable_symbols(
            ["BTCUSDC", "XMRUSDC"],
            {"BTCUSDC": 0.0001, "XMRUSDC": 0.003},
            markets,
            set(),
            include_rejections=True,
        )

        self.assertEqual(eligible, ["BTCUSDC"])
        self.assertEqual(counts["spread"], 1)
        self.assertEqual(rejected[0]["symbol"], "XMRUSDC")
        self.assertEqual(rejected[0]["reason_rejected"], "spread")

    def test_selector_decision_is_persisted_for_dashboard(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "selector_state.json"
            with patch.object(selector, "SELECTOR_STATE_PATH", state_path):
                selector._log_selector_selected_reason("NO_ELIGIBLE_POSITIVE", symbol="XRPUSDC")
                state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(state["last_reason"], "NO_ELIGIBLE_POSITIVE")
        self.assertEqual(state["symbol"], "XRPUSDC")


class ServiceEnvTests(unittest.TestCase):
    def test_write_service_env_does_not_rewrite_unchanged_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".service.env"
            env_path.write_text("PROFILE=strict\nSYMBOL=BTCUSDC\nDRY_RUN=0\n", encoding="utf-8")
            with patch.object(selector, "SERVICE_ENV_PATH", str(env_path)):
                self.assertFalse(selector.write_service_env("BTCUSDC", 0.2, "strict"))
            self.assertEqual(
                env_path.read_text(encoding="utf-8"),
                "PROFILE=strict\nSYMBOL=BTCUSDC\nDRY_RUN=0\n",
            )


class RecentHighFilterTests(unittest.TestCase):
    def test_distance_from_recent_high_pct(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return [
                    [0, "100", "999", "99", "100", "0"],
                    [0, "100", "101", "99", "100", "0"],
                    [0, "100", "102", "99", "101", "0"],
                    [0, "101", "103", "100", "102", "0"],
                    [0, "102", "104", "101", "103", "0"],
                    [0, "103", "105", "102", "104", "0"],
                ]

        with patch.object(selector._SESSION, "get", return_value=Response()):
            self.assertAlmostEqual(
                selector.distance_from_recent_high_pct("BTCUSDC", minutes=5),
                (105.0 - 104.0) / 105.0,
            )

    def test_pick_best_uses_recent_high_candidate_for_observation_only(self):
        ranked = [{
            "symbol": "BTCUSDC",
            "pct": 0.30,
            "spread_pct": 0.02,
            "quote_volume_24h": 2_000_000,
            "trade_count_24h": 6_000,
            "is_toxic": False,
        }]
        with (
            patch.object(selector, "collect_candidates", return_value=ranked),
            patch.object(selector, "rank_candidates", return_value=ranked),
            patch.object(selector, "current_direction_pct", return_value=0.10),
            patch.object(selector, "distance_from_recent_high_pct", return_value=0.0001),
            patch.object(selector, "SELECTOR_MAX_DISTANCE_FROM_5M_HIGH_PCT", 0.0020),
        ):
            chosen, _ = selector.pick_best_candidate({})

        self.assertEqual(chosen["symbol"], "BTCUSDC")
        self.assertTrue(chosen["selector_observe_near_high"])
        self.assertTrue(should_block_near_peak(
            "P",
            PicCheck(True, 0.0, 180, 100.0, "near_peak"),
        ))


if __name__ == "__main__":
    unittest.main()
