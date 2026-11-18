import numpy as np
import pandas as pd

from jrc_allocation import (
    GTAP_GENERATING_ACTIVITIES,
    _aea_value_for_pollutant,
    _split_group_shares,
)


def test_split_group_shares_conserves_total_and_pair_proportions():
    group_shares = pd.Series({
        "nuclear": 0.0,
        "coal": 0.25,
        "gas": 0.30,
        "oil": 0.10,
        "other": 0.05,
        "wind": 0.15,
        "solar": 0.10,
        "hydro": 0.05,
    })
    split_values = pd.Series({
        "GasBL": 3.0, "GasP": 1.0,
        "OilBL": 1.0, "OilP": 3.0,
        "HydroBL": 2.0, "HydroP": 1.0,
    })

    shares = _split_group_shares(group_shares, split_values)

    assert list(shares.index) == GTAP_GENERATING_ACTIVITIES
    assert np.isclose(shares.sum(), 1.0)
    assert np.isclose(shares["GasBL"], 0.225)
    assert np.isclose(shares["GasP"], 0.075)
    assert np.isclose(shares["OilBL"], 0.025)
    assert np.isclose(shares["OilP"], 0.075)
    assert np.isclose(shares["HydroBL"], 0.03333333333333333)


def test_split_group_shares_raises_when_pair_has_no_gtap_support():
    group_shares = pd.Series({
        "nuclear": 0.0, "coal": 0.0, "gas": 0.5, "oil": 0.0,
        "other": 0.0, "wind": 0.5, "solar": 0.0, "hydro": 0.0,
    })
    split_values = pd.Series({"GasBL": 0.0, "GasP": 0.0})

    try:
        _split_group_shares(group_shares, split_values)
    except ValueError as exc:
        assert "Cannot split positive JRC gas generation" in str(exc)
    else:
        raise AssertionError("Expected a ValueError for an unsupported gas split.")


def test_aea_value_extraction_uses_resident_d35_observation():
    # Same layout as the OECD AEA workbook's Table sheet:
    # column 1 = activity label, column 2 = measure, final numeric
    # column = tonnes CO2-equivalent.
    table = pd.DataFrame([
        [None, "Pollutants: Greenhouse gases", None, None, None, None],
        [None, "Electricity, gas, steam and air conditioning supply",
         "Emissions, SEEA (residence principle)", None, 12.0, None],
        [None, "Activity scope: Activities abroad by residents"],
        [None, "Pollutants: Greenhouse gases", None, None, None, None],
        [None, "Electricity, gas, steam and air conditioning supply",
         "Emissions, SEEA (residence principle)", None, 99.0, None],
    ])

    value = _aea_value_for_pollutant(
        table,
        "Greenhouse gases",
        "Electricity, gas, steam and air conditioning supply",
    )
    assert value == 12.0
