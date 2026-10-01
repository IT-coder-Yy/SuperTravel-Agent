import unittest
from datetime import date
from unittest.mock import Mock, patch

from backend.services.exchange_rate_service import (
    clear_exchange_rate_cache,
    quote_currency_to_cny,
)


ECB_FIXTURE = b"""<?xml version='1.0' encoding='UTF-8'?>
<Envelope xmlns='http://www.ecb.int/vocabulary/2002-08-01/eurofxref'>
  <Cube><Cube time='2026-08-19'>
    <Cube currency='JPY' rate='184.62'/>
    <Cube currency='CNY' rate='8.12328'/>
  </Cube></Cube>
</Envelope>
"""


class ExchangeRateServiceTests(unittest.TestCase):
    def setUp(self):
        clear_exchange_rate_cache()

    def tearDown(self):
        clear_exchange_rate_cache()

    @patch("backend.services.exchange_rate_service.requests.get")
    def test_builds_dated_jpy_to_cny_cross_rate_and_caches_ecb_response(self, get_mock):
        response = Mock(content=ECB_FIXTURE)
        response.raise_for_status.return_value = None
        get_mock.return_value = response

        first = quote_currency_to_cny("JPY")
        second = quote_currency_to_cny("JPY")

        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(date(2026, 8, 19), first.as_of)
        self.assertAlmostEqual(0.044, first.cny_per_unit, places=6)
        self.assertEqual(first, second)
        get_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
