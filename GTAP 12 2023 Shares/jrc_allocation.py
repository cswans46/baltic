from __future__ import annotations

from pathlib import Path
import re

import numpy as np
import pandas as pd


GTAP_GENERATING_ACTIVITIES = [
    "NuclearBL",
    "CoalBL",
    "GasBL",
    "WindBL",
    "HydroBL",
    "OilBL",
    "OtherBL",
    "GasP",
    "HydroP",
    "OilP",
    "SolarP",
]

JRC_ISO2 = {"EST": "EE", "LVA": "LV", "LTU": "LT"}
SPLIT_GROUPS = {
    "gas": ("GasBL", "GasP"),
    "hydro": ("HydroBL", "HydroP"),
    "oil": ("OilBL", "OilP"),
}


def find_country_workbook(root: Path, country: str, pattern: str) -> Path:
    """Find one country-specific workbook below the country's data folder."""
    country_root = Path(root) / country
    matches = sorted(country_root.rglob(pattern))
    if not matches:
        raise FileNotFoundError(
            f"No workbook matching {pattern!r} found below {country_root}"
        )
    if len(matches) > 1:
        preferred = [
            p for p in matches
            if "3. Emissions" in p.parts
        ]
        if len(preferred) == 1:
            return preferred[0]
        raise ValueError(
            f"Multiple workbooks match {pattern!r} below {country_root}: "
            + ", ".join(str(p) for p in matches)
        )
    return matches[0]


def _year_column(frame: pd.DataFrame, year: int) -> int:
    for col, value in frame.iloc[0].items():
        try:
            if int(float(value)) == int(year):
                return int(col)
        except (TypeError, ValueError):
            continue
    raise KeyError(f"Year {year} not found in JRC workbook header.")


def _code_column(frame: pd.DataFrame) -> int:
    matches = [
        int(col) for col, value in frame.iloc[0].items()
        if str(value).strip().lower() == "code"
    ]
    if not matches:
        raise KeyError("Could not find the JRC 'Code' column.")
    return matches[-1]


def _jrc_code_values(frame: pd.DataFrame, year: int) -> dict[str, float]:
    year_col = _year_column(frame, year)
    code_col = _code_column(frame)
    values = {}
    for _, row in frame.iterrows():
        code = row.iloc[code_col]
        if not isinstance(code, str) or not code.strip():
            continue
        value = pd.to_numeric(pd.Series([row.iloc[year_col]]), errors="coerce").iloc[0]
        if pd.notna(value):
            values[code.strip()] = float(value)
    return values


def load_jrc_generation_groups(
    powergen_workbook: Path,
    year: int = 2023,
) -> pd.Series:
    """
    Read net electricity generation from JRC-IDEES PowerGen.

    Returns GWh by broad fuel/technology group. Thermal fuel rows are taken
    from the aggregate Thermal sheet, which includes electricity-only and CHP
    plants. Wind, solar, hydro and nuclear are taken from OverviewPG.
    Pumped storage is excluded, consistently with the JRC net-generation
    total that excludes pumped storage.
    """
    powergen_workbook = Path(powergen_workbook)
    thermal = pd.read_excel(powergen_workbook, sheet_name="Thermal", header=None)
    overview = pd.read_excel(powergen_workbook, sheet_name="OverviewPG", header=None)
    thermal_values = _jrc_code_values(thermal, year)
    overview_values = _jrc_code_values(overview, year)

    thermal_prefix = "GenNetElc.GWh.EE.PG.ConvThermal.All."
    fuel_values = {
        code[len(thermal_prefix):]: value
        for code, value in thermal_values.items()
        if code.startswith(thermal_prefix)
    }

    def fuel(*names: str) -> float:
        return sum(fuel_values.get(name, 0.0) for name in names)

    groups = {
        "coal": fuel("Coal", "Lignite"),
        "gas": fuel("Gas", "Biogas", "DerivedGas", "RefineryGas"),
        "oil": fuel("Diesel", "FuelOil"),
        "other": fuel("BiomassWaste", "Waste"),
        "wind": overview_values.get("GenNetElc.GWh.EE.PG.Wind", 0.0),
        "solar": (
            overview_values.get("GenNetElc.GWh.EE.PG.SolarPV", 0.0)
            + overview_values.get("GenNetElc.GWh.EE.PG.SolarThermal", 0.0)
        ),
        "hydro": overview_values.get("GenNetElc.GWh.EE.PG.Hydro", 0.0),
        "nuclear": overview_values.get("GenNetElc.GWh.EE.PG.Nuclear", 0.0),
    }
    result = pd.Series(groups, dtype=float)
    if not np.isfinite(result.to_numpy()).all() or (result < 0).any():
        raise ValueError(f"Invalid JRC net-generation values in {powergen_workbook}.")
    if result.sum() <= 0:
        raise ValueError(f"No positive net electricity generation found for {year}.")
    return result


def _split_group_shares(
    group_shares: pd.Series,
    split_values: pd.Series,
) -> pd.Series:
    """Expand JRC broad groups to the 11 GTAP generation activities."""
    result = pd.Series(0.0, index=GTAP_GENERATING_ACTIVITIES, dtype=float)

    direct = {
        "nuclear": "NuclearBL",
        "coal": "CoalBL",
        "wind": "WindBL",
        "solar": "SolarP",
        "other": "OtherBL",
    }
    for group, activity in direct.items():
        result.loc[activity] = float(group_shares.get(group, 0.0))

    for group, (base, peak) in SPLIT_GROUPS.items():
        total = float(split_values.reindex([base, peak]).fillna(0.0).sum())
        group_share = float(group_shares.get(group, 0.0))
        if group_share > 0 and total <= 0:
            raise ValueError(
                f"Cannot split positive JRC {group} generation between "
                f"{base}/{peak}: GTAP split weights are both zero."
            )
        if total > 0:
            result.loc[base] = group_share * float(split_values.get(base, 0.0)) / total
            result.loc[peak] = group_share * float(split_values.get(peak, 0.0)) / total

    total = result.sum()
    if total <= 0:
        raise ValueError("Expanded JRC generation shares have no positive total.")
    result /= total
    if not np.isclose(result.sum(), 1.0, atol=1e-10):
        raise AssertionError("JRC generation shares do not sum to one.")
    return result


def gtap_output_weights(raw_gtap_levels: dict, country_code: str) -> pd.Series:
    """
    Derive activity output weights from GTAP zero-profit input costs.

    raw_gtap_levels is the data dictionary returned by shares_gams.main():
    VDFB intermediate inputs and EVFB endowment inputs are stacked by activity.
    Under the GTAP zero-profit identity, their sum is the activity's output
    value. These weights are used only to split JRC gas/oil/hydro groups
    between base- and peak-load activities.
    """
    totals = {}
    for activity in GTAP_GENERATING_ACTIVITIES:
        if activity not in raw_gtap_levels:
            totals[activity] = 0.0
            continue
        frame = raw_gtap_levels[activity]
        if country_code not in frame.columns:
            raise KeyError(
                f"{activity}: {country_code} missing from GTAP input-level data."
            )
        totals[activity] = float(
            pd.to_numeric(frame[country_code], errors="coerce").fillna(0.0).sum()
        )
    return pd.Series(totals, dtype=float)


def build_jrc_output_shares(
    powergen_workbook: Path,
    country_code: str,
    raw_gtap_levels: dict,
    year: int = 2023,
) -> pd.Series:
    """Create 11 GTAP output shares using JRC generation and GTAP pair splits."""
    groups = load_jrc_generation_groups(powergen_workbook, year)
    group_shares = groups / groups.sum()
    split_values = gtap_output_weights(raw_gtap_levels, country_code)
    return _split_group_shares(group_shares, split_values)


def _gams_symbol_activity_totals(
    gdx_path: Path,
    symbol_names: list[str],
    country_code: str,
    system_directory: str,
) -> pd.Series:
    """Read activity totals from GTAP GDX symbols using the GAMS Python API."""
    from gams import GamsWorkspace

    gdx_path = Path(gdx_path)
    if not gdx_path.exists():
        raise FileNotFoundError(gdx_path)

    workspace = GamsWorkspace(system_directory=system_directory)
    database = workspace.add_database_from_gdx(str(gdx_path))
    symbols = {str(symbol.name).upper(): symbol for symbol in database}

    totals = pd.Series(0.0, index=GTAP_GENERATING_ACTIVITIES, dtype=float)
    found = []
    for symbol_name in symbol_names:
        symbol = symbols.get(symbol_name.upper())
        if symbol is None:
            continue
        found.append(symbol_name)
        for record in symbol:
            keys = tuple(str(key) for key in record.keys)
            # CO2 MDF/MMF: fuel, activity, region.
            # Non-CO2 EMI_IO: gas, fuel, activity, region.
            if len(keys) == 3:
                activity, region = keys[1], keys[2]
            elif len(keys) == 4:
                activity, region = keys[2], keys[3]
            else:
                continue
            if region == country_code and activity in totals.index:
                totals.loc[activity] += float(record.value)

    if not found:
        raise KeyError(
            f"None of the required symbols {symbol_names} found in {gdx_path}."
        )
    if not np.isfinite(totals.to_numpy()).all() or (totals < 0).any():
        raise ValueError(f"Invalid GTAP emissions values in {gdx_path}.")
    return totals


def load_gtap_emissions_weights(
    co2_gdx: Path,
    nonco2_gdx: Path,
    country_code: str,
    system_directory: str = r"C:\GAMS\51",
) -> tuple[pd.Series, pd.Series]:
    """
    Return GTAP activity emission totals for CO2 and non-CO2.

    CO2 uses MDF and MMF (domestic/imported fuel combustion accounts).
    Non-CO2 uses EMI_IO (gas x fuel x activity x region), avoiding the
    separate endowment, output, household and land-use accounts.
    """
    co2 = _gams_symbol_activity_totals(
        co2_gdx, ["MDF", "MMF"], country_code, system_directory
    )
    nonco2 = _gams_symbol_activity_totals(
        nonco2_gdx, ["EMI_IO"], country_code, system_directory
    )
    return co2, nonco2


def _aea_value_for_pollutant(
    table: pd.DataFrame,
    pollutant: str,
    activity_label: str,
) -> float:
    pollutant_rows = [
        i for i, value in table.iloc[:, 1].items()
        if isinstance(value, str)
        and value.strip().lower().startswith("pollutants:")
        and pollutant.lower() in value.lower()
    ]
    if not pollutant_rows:
        raise KeyError(f"Pollutant section {pollutant!r} not found in OECD AEA workbook.")

    # The first section is the resident-activity account. Later sections
    # refer to activities abroad/by residents and are not used here.
    header_row = pollutant_rows[0]
    end_candidates = [
        i for i in range(header_row + 1, len(table))
        if isinstance(table.iloc[i, 1], str)
        and (
            table.iloc[i, 1].strip().lower().startswith("pollutants:")
            or table.iloc[i, 1].strip().lower().startswith("activity scope:")
        )
    ]
    end_row = min(end_candidates) if end_candidates else len(table)

    matches = []
    for i in range(header_row + 1, end_row):
        label = table.iloc[i, 1]
        measure = table.iloc[i, 2] if table.shape[1] > 2 else None
        if (
            isinstance(label, str)
            and activity_label.lower() in label.lower()
            and isinstance(measure, str)
            and measure.strip() == "Emissions, SEEA (residence principle)"
        ):
            numeric = pd.to_numeric(table.iloc[i, 3:], errors="coerce").dropna()
            if not numeric.empty:
                matches.append(float(numeric.iloc[-1]))

    if len(matches) != 1:
        raise ValueError(
            f"Expected one {pollutant} observation for {activity_label!r}; "
            f"found {len(matches)}."
        )
    return matches[0]


def load_oecd_aea_d35_emissions(
    workbook: Path,
    activity_label: str = "Electricity, gas, steam and air conditioning supply",
) -> tuple[float, float]:
    """
    Read resident-sector D35 GHG (t CO2e) and CO2 (tonnes) from the OECD AEA
    workbook. The difference is the non-CO2 component in CO2-equivalent terms.
    """
    table = pd.read_excel(workbook, sheet_name="Table", header=None)
    ghg = _aea_value_for_pollutant(table, "Greenhouse gases", activity_label)
    co2 = _aea_value_for_pollutant(table, "Carbon dioxide", activity_label)
    nonco2 = ghg - co2
    if co2 < 0 or nonco2 < -1e-6:
        raise ValueError(
            f"OECD AEA D35 values are inconsistent: GHG={ghg}, CO2={co2}, "
            f"implied non-CO2={nonco2}."
        )
    return co2, max(nonco2, 0.0)


def allocate_oecd_aea_emissions(
    powergen_workbook: Path,
    aea_workbook: Path,
    co2_gtap_totals: pd.Series,
    nonco2_gtap_totals: pd.Series,
    year: int = 2023,
) -> pd.DataFrame:
    """
    Allocate OECD AEA D35 emissions to 11 GTAP generating activities.

    JRC net-generation shares determine each broad technology group's share.
    GTAP GDX emissions determine only the base/peak split within gas, oil and
    hydro. CO2 and non-CO2 use their respective GTAP emission accounts.
    """
    groups = load_jrc_generation_groups(powergen_workbook, year)
    group_shares = groups / groups.sum()

    co2_shares = _split_group_shares(group_shares, co2_gtap_totals)
    nonco2_shares = _split_group_shares(group_shares, nonco2_gtap_totals)
    co2_total, nonco2_total = load_oecd_aea_d35_emissions(aea_workbook)

    result = pd.DataFrame(index=GTAP_GENERATING_ACTIVITIES)
    result.index.name = "GTAP_activity"
    result["JRC_group"] = [
        next(
            (group for group, pair in SPLIT_GROUPS.items() if activity in pair),
            {
                "NuclearBL": "nuclear",
                "CoalBL": "coal",
                "WindBL": "wind",
                "SolarP": "solar",
                "OtherBL": "other",
            }.get(activity, ""),
        )
        for activity in result.index
    ]
    result["JRC_generation_share"] = co2_shares.index.map(
        lambda activity: (
            group_shares.get(
                next((g for g, pair in SPLIT_GROUPS.items() if activity in pair),
                     result.loc[activity, "JRC_group"]),
                0.0,
            )
        )
    )
    result["CO2_share"] = co2_shares
    result["nonCO2_share"] = nonco2_shares
    result["CO2_tonnes"] = result["CO2_share"] * co2_total
    result["nonCO2_tCO2e"] = result["nonCO2_share"] * nonco2_total
    result["GHG_tCO2e"] = result["CO2_tonnes"] + result["nonCO2_tCO2e"]
    result["AEA_CO2_total_tonnes"] = co2_total
    result["AEA_nonCO2_total_tCO2e"] = nonco2_total

    if not np.isclose(result["CO2_tonnes"].sum(), co2_total, rtol=1e-9, atol=1e-6):
        raise AssertionError("Allocated CO2 does not conserve the OECD AEA total.")
    if not np.isclose(result["nonCO2_tCO2e"].sum(), nonco2_total, rtol=1e-9, atol=1e-6):
        raise AssertionError("Allocated non-CO2 does not conserve the OECD AEA total.")
    return result.reset_index()
