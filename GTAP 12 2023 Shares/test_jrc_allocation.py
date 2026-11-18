import numpy as np
import pandas as pd

from jrc_allocation import (
    GTAP_GENERATING_ACTIVITIES,
    GTAP_EMISSIONS_ACTIVITIES,
    _aea_value_for_pollutant,
    _split_group_shares,
    load_jrc_emission_balance_co2_groups,
    allocate_oecd_aea_emissions,
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



def test_emission_balance_reader_uses_parent_codes_and_excludes_bioenergy(tmp_path):
    path = tmp_path / "emissions.xlsx"
    rows = [
        ["Electricity only", "TI_EHG_E_E", 2022, 2023],
        ["Total", "TOTAL", 100.0, 100.0],
        ["Hard coal", "C0100", 0.0, 0.0],
        ["Manufactured gases", "C0350-0370", 10.0, 10.0],
        ["Peat", "P1000", 5.0, 5.0],
        ["Oil shale", "S2000", 20.0, 20.0],
        ["Oil products", "O4000XBIO", 10.0, 10.0],
        ["Natural gas", "G3000", 5.0, 5.0],
        ["Non-renewable waste", "W6100_6220", 0.0, 0.0],
        ["Renewables and biofuels", "RA000", 50.0, 50.0],
    ]
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(rows).to_excel(
            writer, sheet_name="TI_EHG_E_E", header=False, index=False
        )

    groups = load_jrc_emission_balance_co2_groups(
        path, "TI_EHG_E_E", year=2023
    )
    assert np.isclose(groups["coal"], 20.0)
    assert np.isclose(groups["gas"], 15.0)
    assert np.isclose(groups["oil"], 10.0)
    assert np.isclose(groups["other"], 5.0)
    assert np.isclose(groups.sum(), 50.0)


def test_emissions_allocation_assigns_heat_only_to_gdt_and_chp_to_electricity(monkeypatch):
    import jrc_allocation as ja

    electricity = pd.Series({
        "coal": 20.0, "gas": 30.0, "oil": 10.0, "other": 5.0,
        "wind": 0.0, "solar": 0.0, "hydro": 0.0, "nuclear": 0.0,
    })
    heat = pd.Series({
        "coal": 0.0, "gas": 10.0, "oil": 10.0, "other": 0.0,
        "wind": 0.0, "solar": 0.0, "hydro": 0.0, "nuclear": 0.0,
    })
    calls = iter([electricity * 0.6, electricity * 0.4, heat])
    monkeypatch.setattr(
        ja, "load_jrc_emission_balance_co2_groups",
        lambda *args, **kwargs: next(calls),
    )
    monkeypatch.setattr(
        ja, "load_oecd_aea_d35_emissions", lambda *args, **kwargs: (1000.0, 500.0)
    )

    co2_gtap = pd.Series(1.0, index=GTAP_EMISSIONS_ACTIVITIES)
    nonco2_gtap = pd.Series(1.0, index=GTAP_EMISSIONS_ACTIVITIES)
    split = pd.Series({
        "GasBL": 1.0, "GasP": 1.0,
        "OilBL": 1.0, "OilP": 1.0,
        "HydroBL": 1.0, "HydroP": 1.0,
    })
    monkeypatch.setattr(ja, "_split_group_shares", lambda groups, weights: (
        pd.Series(1 / len(GTAP_GENERATING_ACTIVITIES),
                  index=GTAP_GENERATING_ACTIVITIES)
    ))

    result = allocate_oecd_aea_emissions(
        None, None, co2_gtap, nonco2_gtap,
        emission_balance_workbook="dummy.xlsx",
    ).set_index("GTAP_activity")

    assert list(result.index) == GTAP_EMISSIONS_ACTIVITIES
    assert np.isclose(result.loc["GDT", "CO2_share"], 20.0 / 85.0)
    assert np.isclose(
        result.loc[GTAP_GENERATING_ACTIVITIES, "CO2_share"].sum(),
        65.0 / 85.0,
    )
    assert np.isclose(result["CO2_share"].sum(), 1.0)
    assert np.isclose(result["nonCO2_share"].sum(), 1.0)
