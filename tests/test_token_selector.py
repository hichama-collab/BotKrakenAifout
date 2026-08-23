import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import TokenProfileSelector as selector


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

    def test_top_mover_fallback_selects_highest_positive_variation(self):
        movers = [
            {"symbol": "ETHUSDC", "pct": 0.10, "spread_pct": 0.01},
            {"symbol": "VIRTUALUSDC", "pct": 2.42, "spread_pct": 0.15},
            {"symbol": "BTCUSDC", "pct": 0.13, "spread_pct": 0.01},
        ]
        with (
            patch.object(selector, "SELECTOR_TOP_MOVER_FALLBACK", True),
            patch.object(selector, "collect_top_movers", return_value=movers),
        ):
            chosen, ranked = selector.choose_top_mover_fallback(set(), {})

        self.assertEqual(chosen["symbol"], "VIRTUALUSDC")
        self.assertEqual([item["symbol"] for item in ranked], ["VIRTUALUSDC", "BTCUSDC", "ETHUSDC"])

    def test_top_mover_fallback_reuses_tradability_gates(self):
        expected = [{"symbol": "SOLUSDC", "pct": 0.42, "spread_pct": 0.03}]
        with patch.object(selector, "collect_candidates", return_value=expected):
            assert selector.collect_top_movers({"BTCUSDC"}) == expected


class KrakenTickerTests(unittest.TestCase):
    def test_market_stats_uses_kraken_utc_session_open(self):
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

        self.assertEqual(stats["BTCUSDC"]["change_pct_24h"], 10.0)


class TradableAnchorTests(unittest.TestCase):
    def test_anchor_replaces_only_an_untradable_active_symbol(self):
        markets = {
            "BTCUSDC": {
                "last_price": 100.0,
                "quote_volume_24h": 9_000_000.0,
                "trade_count_24h": 10_000,
                "change_pct_24h": 1.0,
            },
            "XMRUSDC": {
                "last_price": 100.0,
                "quote_volume_24h": 100_000.0,
                "trade_count_24h": 100,
                "change_pct_24h": 1.0,
            },
        }
        with (
            patch.object(selector, "get_symbols_usdc_trading", return_value=["BTCUSDC", "XMRUSDC"]),
            patch.object(selector, "get_spread_map", return_value={"BTCUSDC": 0.0001, "XMRUSDC": 0.003}),
            patch.object(selector, "get_market_stats_map", return_value=markets),
        ):
            anchor, current_is_tradable = selector.choose_tradable_anchor("XMRUSDC")
            no_anchor, btc_is_tradable = selector.choose_tradable_anchor("BTCUSDC")

        self.assertFalse(current_is_tradable)
        self.assertEqual(anchor["symbol"], "BTCUSDC")
        self.assertIsNone(no_anchor)
        self.assertTrue(btc_is_tradable)


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

    def test_pick_best_rejects_candidate_too_close_to_high(self):
        ranked = [{"symbol": "BTCUSDC", "pct": 0.30, "spread_pct": 0.02, "is_toxic": False}]
        with (
            patch.object(selector, "collect_candidates", return_value=ranked),
            patch.object(selector, "rank_candidates", return_value=ranked),
            patch.object(selector, "current_direction_pct", return_value=0.10),
            patch.object(selector, "distance_from_recent_high_pct", return_value=0.0001),
            patch.object(selector, "SELECTOR_MAX_DISTANCE_FROM_5M_HIGH_PCT", 0.0020),
        ):
            chosen, _ = selector.pick_best_candidate({})

        self.assertIsNone(chosen)


if __name__ == "__main__":
    unittest.main()
