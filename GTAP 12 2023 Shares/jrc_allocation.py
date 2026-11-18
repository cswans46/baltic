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
    country_code: str = "EST",
    include_heat: bool = False,
) -> pd.Series:
    """
    Read net electricity generation from JRC-IDEES PowerGen.

    Returns GWh by broad fuel/technology group. Thermal fuel rows are taken
    from the aggregate Thermal sheet, which includes electricity-only and CHP
    plants. Wind, solar, hydro and nuclear are taken from OverviewPG.
    Pumped storage is excluded, consistently with the JRC net-generation
    total that excludes pumped storage. If include_heat=True, also add CHP
    heat output and heat-only plant output, grouped by fuel. This is the
    appropriate basis for allocating OECD D35 emissions, which also covers
    steam and district-heat supply.
    """
    powergen_workbook = Path(powergen_workbook)
    thermal = pd.read_excel(powergen_workbook, sheet_name="Thermal", header=None)
    overview = pd.read_excel(powergen_workbook, sheet_name="OverviewPG", header=None)
    thermal_values = _jrc_code_values(thermal, year)
    overview_values = _jrc_code_values(overview, year)

    if country_code not in JRC_ISO2:
        raise KeyError(f"Unsupported GTAP country code: {country_code}")
    iso2 = JRC_ISO2[country_code]
    thermal_prefix = ff"GenNetElc.GWh.{iso2}.PG.ConvThermal.All."
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
        "wind": overview_values.get(f"GenNetElc.GWh.{iso2}.PG.Wind", 0.0),
        "solar": (
            overview_values.get(f"GenNetElc.GWh.{iso2}.PG.SolarPV", 0.0)
            + overview_values.get(f"GenNetElc.GWh.{iso2}.PG.SolarThermal", 0.0)
        ),
        "hydro": overview_values.get(f"GenNetElc.GWh.{iso2}.PG.Hydro", 0.0),
        "nuclear": overview_values.get(f"GenNetElc.GWh.{iso2}.PG.Nuclear", 0.0),
    }

    if include_heat:
        # Add CHP heat output and heat-only plant output. The former is
        # reported on the Thermal sheet; the latter on the DistHeat sheet.
        chp_frame = pd.read_excel(
            powergen_workbook, sheet_name="Thermal_CHP", header=None
        )
        chp_values = _jrc_code_values(chp_frame, year)
        chp_prefix = f"GenStm_CHP.GWh.{iso2}.PG.ConvThermal.CHP."
        chp_heat = {
            code[len(chp_prefix):]: value
            for code, value in chp_values.items()
            if code.startswith(chp_prefix)
        }
        distheat = pd.read_excel(powergen_workbook, sheet_name="DistHeat", header=None)
        distheat_values = _jrc_code_values(distheat, year)
        dh_prefix = f"GenStm.GWh.{iso2}.DH.ConvThermal."
        dh_heat = {
            code[len(dh_prefix):]: value
            for code, value in distheat_values.items()
            if code.startswith(dh_prefix)
        }

        groups["coal"] += sum(chp_heat.get(k, 0.0) for k in ("Coal", "Lignite"))
        groups["gas"] += sum(chp_heat.get(k, 0.0) for k in ("Gas", "Biogas", "DerivedGas", "RefineryGas"))
        groups["oil"] += sum(chp_heat.get(k, 0.0) for k in ("Diesel", "FuelOil"))
        groups["other"] += sum(chp_heat.get(k, 0.0) for k in ("BiomassWaste", "Waste"))

        groups["coal"] += sum(dh_heat.get(k, 0.0) for k in ("EFCoal", "EFLignite"))
        groups["gas"] += sum(dh_heat.get(k, 0.0) for k in ("EFGas", "EFBiogas", "EFDerivedGas"))
        groups["oil"] += sum(dh_heat.get(k, 0.0) for k in ("EFDiesel", "EFFuelOil"))
        groups["other"] += sum(dh_heat.get(k, 0.0) for k in ("EFWoodWaste", "EFWasteNR"))

    result = pd.Series(groups, dtype=float)
    if not np.isfinite(result.to_numpy()).all() or (result < 0).any():
        raise ValueError(f"Invalid JRC net-generation values in {powergen_workbook}.")
    if result.sum() <= 0:
        raise ValueError(f"No positive net electricity generation found for {year}.")
    return result


def load_jrc_co2_emission_groups(
    powergen_workbook: Path,
    year: int = 2023,
    country_code: str = "EST",
) -> pd.Series:
    """
    Read JRC-IDEES fossil CO2 emissions for power and heat generation.

    Uses all conventional thermal power plants (electricity-only + CHP) from
    Thermal and heat-only plants from DistHeat. Biomass CO2 is excluded,
    consistent with the OECD AEA greenhouse-gas account used here.
    """
    if country_code not in JRC_ISO2:
        raise KeyError(f"Unsupported GTAP country code: {country_code}")
    iso2 = JRC_ISO2[country_code]

    thermal = pd.read_excel(powergen_workbook, sheet_name="Thermal", header=None)
    distheat = pd.read_excel(powergen_workbook, sheet_name="DistHeat", header=None)
    thermal_values = _jrc_code_values(thermal, year)
    heat_values = _jrc_code_values(distheat, year)

    thermal_prefix = f"Emi.ktCO2.{iso2}.PG.ConvThermal.All."
    heat_prefix = f"Emi.ktCO2.{iso2}.DH.ConvThermal."
    thermal_emissions = {
        code[len(thermal_prefix):]: value
        for code, value in thermal_values.items()
        if code.startswith(thermal_prefix)
    }
    heat_emissions = {
        code[len(heat_prefix):]: value
        for code, value in heat_values.items()
        if code.startswith(heat_prefix)
    }

    groups = {
        "coal": (
            thermal_emissions.get("Coal", 0.0)
            + thermal_emissions.get("Lignite", 0.0)
            + heat_emissions.get("EFCoal", 0.0)
            + heat_emissions.get("EFLignite", 0.0)
        ),
        "gas": (
            thermal_emissions.get("Gas", 0.0)
            + thermal_emissions.get("Biogas.EFBiogas", 0.0)
            + thermal_emissions.get("DerivedGas.EFDerivedGas", 0.0)
            + thermal_emissions.get("RefineryGas.EFRefineryGas", 0.0)
            + heat_emissions.get("EFGas", 0.0)
            + heat_emissions.get("EFDerivedGas", 0.0)
            + heat_emissions.get("EFRefineryGas", 0.0)
        ),
        "oil": (
            thermal_emissions.get("Diesel", 0.0)
            + thermal_emissions.get("FuelOil", 0.0)
            + heat_emissions.get("EFDiesel", 0.0)
            + heat_emissions.get("EFFuelOil", 0.0)
        ),
        # JRC reports zero CO2 for biomass; retain only fossil/non-renewable
        # waste emissions in the GTAP OtherBL category.
        "other": (
            thermal_emissions.get("Waste", 0.0)
            + heat_emissions.get("EFWasteNR", 0.0)
        ),
        "wind": 0.0,
        "solar": 0.0,
        "hydro": 0.0,
        "nuclear": 0.0,
    }
    result = pd.Series(groups, dtype=float)
    if not np.isfinite(result.to_numpy()).all() or (result < 0).any():
        raise ValueError(f"Invalid JRC CO2 emissions in {powergen_workbook}.")
    if result.sum() <= 0:
        raise ValueError(f"No positive JRC CO2 emissions found for {year}.")
    return result


def load_jrc_emission_balance_co2_total(
    emission_balance_workbook: Path,
    year: int = 2023,
) -> float:
    """Read the JRC EmissionBalance TI_EHG_E total CO2 emissions (kt CO2)."""
    frame = pd.read_excel(
        emission_balance_workbook, sheet_name="TI_EHG_E", header=None
    )
    year_col = _year_column(frame, year)
    matches = frame.loc[
        frame.iloc[:, 0].astype(str).str.strip().str.lower().eq("total")
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one 'Total' row in TI_EHG_E of {emission_balance_workbook}; "
            f"found {len(matches)}."
        )
    value = pd.to_numeric(
        pd.Series([matches.iloc[0, year_col]]), errors="coerce"
    ).iloc[0]
    if pd.isna(value) or value < 0:
        raise ValueError(
            f"Invalid TI_EHG_E CO2 total for {year} in {emission_balance_workbook}."
        )
    return float(value)


def validate_jrc_co2_emissions(
    powergen_workbook: Path,
    emission_balance_workbook: Path,
    year: int = 2023,
    country_code: str = "EST",
    tolerance_kt: float = 0.5,
) -> tuple[float, float]:
    """
    Cross-check PowerGen fuel-specific thermal + heat-only CO2 against
    EmissionBalance's aggregate TI_EHG_E CO2 total.
    """
    powergen_total = float(
        load_jrc_co2_emission_groups(
            powergen_workbook, year, country_code
        ).sum()
    )
    balance_total = load_jrc_emission_balance_co2_total(
        emission_balance_workbook, year
    )
    difference = powergen_total - balance_total
    if abs(difference) > tolerance_kt:
        raise ValueError(
            f"JRC CO2 cross-check failed for {country_code} {year}: "
            f"PowerGen={powergen_total:.6f} kt, "
            f"EmissionBalance={balance_total:.6f} kt, "
            f"difference={difference:.6f} kt."
        )
    return powergen_total, balance_total


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
    groups = load_jrc_generation_groups(powergen_workbook, year, country_code)
    group_shares = groups / groups.sum()
    split_values = gtap_output_weights(raw_gtap_levels, country_code)
    return _split_group_shares(group_shares, split_values)


def _gams_symbol_activity_totals(
    gdx_path: Path,
    symbol_names: list[str],
    country_code: str,
    system_directory: str,
    gwp_ar6: dict[str, float] | None = None,
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
                value = float(record.value)
                if len(keys) == 4 and gwp_ar6 is not None:
                    gas = keys[0]
                    if gas.upper() == "CO2":
                        continue
                    if gas not in gwp_ar6:
                        raise KeyError(f"Missing AR6 GWP for GTAP gas {gas!r} in {gdx_path}.")
                    value *= gwp_ar6[gas]
                totals.loc[activity] += value

    if not found:
        raise KeyError(
            f"None of the required symbols {symbol_names} found in {gdx_path}."
        )
    if not np.isfinite(totals.to_numpy()).all() or (totals < 0).any():
        raise ValueError(f"Invalid GTAP emissions values in {gdx_path}.")
    return totals


def _load_gwp_ar6(nonco2_gdx: Path, country_code: str, system_directory: str) -> dict[str, float]:
    """Read country-specific AR6 global-warming potentials from the NCO2 GDX."""
    from gams import GamsWorkspace

    workspace = GamsWorkspace(system_directory=system_directory)
    database = workspace.add_database_from_gdx(str(Path(nonco2_gdx)))
    symbols = {str(symbol.name).upper(): symbol for symbol in database}
    symbol = symbols.get("GWP")
    if symbol is None:
        raise KeyError(f"GWP symbol not found in {nonco2_gdx}.")
    result = {}
    for record in symbol:
        keys = tuple(str(key) for key in record.keys)
        if len(keys) == 3 and keys[1] == country_code and keys[2].upper() == "AR6":
            result[keys[0]] = float(record.value)
    if not result:
        raise ValueError(f"No country-specific AR6 GWP records found for {country_code}.")
    return result


def load_gtap_emissions_weights(
    co2_gdx: Path,
    nonco2_gdx: Path,
    country_code: str,
    system_directory: str = r"C:\\GAMS\\51",
) -> tuple[pd.Series, pd.Series]:
    """
    Return GTAP activity emission totals for CO2 and non-CO2.

    CO2 uses MDF and MMF (domestic/imported fuel combustion accounts).
    Non-CO2 uses EMI_IO, excludes CO2, and converts remaining gases to
    CO2-equivalent using country-specific AR6 GWP records.
    """
    co2 = _gams_symbol_activity_totals(
        co2_gdx, ["MDF", "MMF"], country_code, system_directory
    )
    gwp_ar6 = _load_gwp_ar6(nonco2_gdx, country_code, system_directory)
    nonco2 = _gams_symbol_activity_totals(
        nonco2_gdx, ["EMI_IO"], country_code, system_directory, gwp_ar6=gwp_ar6
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
    emission_balance_workbook: Path | None = None,
    year: int = 2023,
    country_code: str = "EST",
) -> pd.DataFrame:
    """
    Allocate OECD AEA D35 emissions to 11 GTAP generating activities.

    JRC net-generation shares determine each broad technology group's share.
    GTAP GDX emissions determine only the base/peak split within gas, oil and
    hydro. CO2 and non-CO2 use their respective GTAP emission accounts.
    """
    # CO2 allocation uses JRC's fuel-specific fossil CO2 emissions from
    # power plants and heat-only plants. Non-CO2 allocation uses JRC combined
    # electricity + heat generation by fuel as the activity proxy.
    co2_groups = load_jrc_co2_emission_groups(
        powergen_workbook, year, country_code
    )
    co2_group_shares = co2_groups / co2_groups.sum()
    generation_groups = load_jrc_generation_groups(
        powergen_workbook, year, country_code, include_heat=True
    )
    generation_group_shares = generation_groups / generation_groups.sum()

    jrc_co2_powergen_total = np.nan
    jrc_co2_balance_total = np.nan
    if emission_balance_workbook is not None:
        jrc_co2_powergen_total, jrc_co2_balance_total = validate_jrc_co2_emissions(
            powergen_workbook,
            emission_balance_workbook,
            year=year,
            country_code=country_code,
        )

    co2_shares = _split_group_shares(co2_group_shares, co2_gtap_totals)
    nonco2_shares = _split_group_shares(generation_group_shares, nonco2_gtap_totals)
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
    result["JRC_CO2_group_share"] = co2_shares.index.map(
        lambda activity: co2_group_shares.get(
            next((g for g, pair in SPLIT_GROUPS.items() if activity in pair),
                 result.loc[activity, "JRC_group"]),
            0.0,
        )
    )
    result["JRC_generation_share"] = nonco2_shares.index.map(
        lambda activity: generation_group_shares.get(
            next((g for g, pair in SPLIT_GROUPS.items() if activity in pair),
                 result.loc[activity, "JRC_group"]),
            0.0,
        )
    )
    result["CO2_share"] = co2_shares
    result["nonCO2_share"] = nonco2_shares
    result["CO2_tonnes"] = result["CO2_share"] * co2_total
    result["nonCO2_tCO2e"] = result["nonCO2_share"] * nonco2_total
    result["GHG_tCO2e"] = result["CO2_tonnes"] + result["nonCO2_tCO2e"]
    result["AEA_CO2_total_tonnes"] = co2_total
    result["AEA_nonCO2_total_tCO2e"] = nonco2_total
    result["JRC_CO2_total_PowerGen_kt"] = jrc_co2_powergen_total
    result["JRC_CO2_total_EmissionBalance_kt"] = jrc_co2_balance_total

    if not np.isclose(result["CO2_tonnes"].sum(), co2_total, rtol=1e-9, atol=1e-6):
        raise AssertionError("Allocated CO2 does not conserve the OECD AEA total.")
    if not np.isclose(result["nonCO2_tCO2e"].sum(), nonco2_total, rtol=1e-9, atol=1e-6):
        raise AssertionError("Allocated non-CO2 does not conserve the OECD AEA total.")
    return result.reset_index()
