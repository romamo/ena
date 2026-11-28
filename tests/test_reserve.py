import datetime as dt

from ena.models import Forecast, ForecastWeek, OfferKind
from ena.rules.forecast import CashForecast
from ena.rules.reserve import reserve_offer

START = dt.date(2026, 11, 2)


def cash_forecast(
    closings: list[int],
    *,
    due: dt.date,
    instalment_cents: int = 40_000,
    buffer_cents: int = 55_000,
) -> CashForecast:
    """A forecast whose weeks close at `closings`, with nothing coming in or going out."""
    threshold = instalment_cents + buffer_cents
    weeks = tuple(
        ForecastWeek(
            week_start=START + dt.timedelta(weeks=n),
            opening_cents=closing,
            in_cents=0,
            out_cents=0,
            closing_cents=closing,
        )
        for n, closing in enumerate(closings)
    )
    lean = next((week.week_start for week in weeks if week.closing_cents < threshold), None)
    return CashForecast(
        forecast=Forecast(weeks=weeks, lean_week=lean, reason="test"),
        instalment_cents=instalment_cents,
        instalment_due=due,
        expected_card_sales_cents=0,
        buffer_cents=buffer_cents,
        threshold_cents=threshold,
    )


def test_lean_week_before_the_instalment_reserves_it() -> None:
    cash = cash_forecast([200_000, 34_969, 200_000], due=dt.date(2026, 11, 25))
    offer = reserve_offer(cash)
    assert offer is not None
    assert offer.kind is OfferKind.RESERVE
    assert offer.amount_cents == 40_000
    assert offer.term_days == 24  # 2 Nov to 25 Nov, both counted
    assert offer.simulated is False
    assert offer.id == "reserve-20261125"
    assert dict(offer.basis) == {
        "instalment_cents": 40_000,
        "lean_week_closing_cents": 34_969,
        "buffer_cents": 55_000,
        "threshold_cents": 95_000,
        "days_to_instalment": 24,
    }


def test_lean_week_holding_the_instalment_reserves_it() -> None:
    # The lean week starts Mon 9 Nov; the instalment is due Sun 15 Nov, in that week.
    offer = reserve_offer(cash_forecast([200_000, 0], due=dt.date(2026, 11, 15)))
    assert offer is not None
    assert offer.term_days == 14


def test_lean_week_starting_on_the_due_date_reserves_it() -> None:
    offer = reserve_offer(cash_forecast([0], due=START))
    assert offer is not None
    assert offer.term_days == 1


def test_lean_week_after_the_instalment_reserves_nothing() -> None:
    # The instalment is due Sun 8 Nov; the lean week starts Mon 9 Nov.
    assert reserve_offer(cash_forecast([200_000, 0], due=dt.date(2026, 11, 8))) is None


def test_no_lean_week_reserves_nothing() -> None:
    assert reserve_offer(cash_forecast([95_000, 95_000], due=dt.date(2026, 11, 4))) is None
