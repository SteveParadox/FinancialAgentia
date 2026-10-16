import math
from statistics import stdev

import pytest
from dexter_py.tools.calculations import (
    returns, compound_return, cagr, moving_average, volatility, max_drawdown,
    sharpe_ratio, correlation, ratio, benchmark_relative,
)


def test_known_reference_cases():
    assert returns([100, 110, 99]) == pytest.approx([0.1, -0.1])
    assert compound_return([0.1, -0.1]) == pytest.approx(-0.01)
    assert cagr(100, 121, 730.5) == pytest.approx(0.1)
    assert moving_average([1, 2, 3, 4], 3) == 3
    assert max_drawdown([100, 120, 60, 90]) == -0.5
    assert volatility([0.1, -0.1]) == pytest.approx(math.sqrt(0.02) * math.sqrt(252))
    assert sharpe_ratio([0.01, 0.03], 0) == pytest.approx(0.02 / math.sqrt(0.0002) * math.sqrt(252))
    assert correlation([1, 2, 3], [3, 2, 1]) == pytest.approx(-1)
    assert ratio(20, 100) == 0.2
    assert ratio(100, 5) == 20
    assert benchmark_relative([100, 110, 120], [100, 105, 110]) == pytest.approx(0.1)


def test_undefined_ratios_never_become_infinity():
    assert sharpe_ratio([0, 0]) is None
    assert ratio(2, 0) is None
    assert correlation([1, 1], [2, 3]) is None


@pytest.mark.parametrize('prices', [[100, None], [0, 2], [True, 2], [float('nan'), 3], [2]])
def test_missing_invalid_prices_are_rejected(prices):
    with pytest.raises((ValueError, TypeError)):
        returns(prices)
