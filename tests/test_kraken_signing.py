import base64
import hashlib
import hmac
from urllib.parse import urlencode

import pytest

from exchange.kraken import Kraken, KrakenApiError


def test_kraken_signature_is_deterministic():
    secret = base64.b64encode(b"secret").decode()
    client = Kraken("key", secret)
    data = {"nonce": "1616492376594", "ordertype": "limit", "pair": "XBTUSDC"}
    postdata = urlencode(data)
    expected = base64.b64encode(
        hmac.new(
            b"secret",
            b"/0/private/AddOrder" + hashlib.sha256((data["nonce"] + postdata).encode()).digest(),
            hashlib.sha512,
        ).digest()
    ).decode()
    assert client.sign("/0/private/AddOrder", data) == expected


def test_kraken_api_error_carries_endpoint_status_and_errors():
    err = KrakenApiError(["EOrder:Insufficient funds"], "/0/private/AddOrder", 200, "{}")

    assert err.endpoint == "/0/private/AddOrder"
    assert err.status_http == 200
    assert "Insufficient funds" in str(err)


def test_trade_volume_converts_kraken_percentage_schedule_to_decimal_rates():
    client = Kraken("key", base64.b64encode(b"secret").decode())
    client.resolve_pair = lambda symbol: type("Pair", (), {"symbol": "ADAUSDC", "pair_id": "ADAUSDC"})()
    client.post = lambda path, params: {
        "fees": {"ADAUSDC": {"fee": "0.35"}},
        "fees_maker": {"ADAUSDC": {"fee": "0.20"}},
    }

    rates = client.trade_fee_rates("ADAUSDC")

    assert rates["taker"] == pytest.approx(0.0035)
    assert rates["maker"] == pytest.approx(0.002)
