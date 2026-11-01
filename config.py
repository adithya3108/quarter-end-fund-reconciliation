"""Judgment calls, kept in one place so they are easy to see and change. See ASSUMPTIONS.md."""
from decimal import Decimal

FUND_CODE = "GPF"
VALUATION_DATE = "2022-12-31"

# Hint 1: 31 Dec 2022 was a Saturday; the ECB publishes no rate at weekends. We use the last
# published reference rate on or before the valuation date: Friday 30 Dec 2022.
EURUSD_RATE = Decimal("1.0666")
EURUSD_RATE_DATE = "2022-12-30"
EURUSD_SOURCE = ("ECB euro foreign exchange reference rate, USD, 30 Dec 2022 "
                 "(https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?startPeriod=2022-12-27&endPeriod=2023-01-04; "
                 "page: https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/"
                 "html/eurofxref-graph-usd.en.html; also Official Journal of the EU 2023/C 1/01). "
                 "Retrieved 2026-11-01: 30 Dec 2022 = 1.0666; no 31 Dec rate; next is 2 Jan 2023 = 1.0683.")

# Hint 3: materiality for R1, measured in the company's own currency.
MATERIALITY_THRESHOLD_PCT = Decimal("15")

DEFAULT_AGENT_A_PORT = 9101
DEFAULT_AGENT_B_PORT = 9102
