"""
Step 6 of the pipeline: project every balanced disaggregated table to 2030 (BAU),
rebalance with RAS, grow the environmental extension and run the price model.

Called once from application.main() AFTER the initial 2022 balancing.

Environmental extension
-----------------------
Emissions follow the paper (Eq. 3-4): constant emission intensity per unit of
output, so each sector's emissions grow with its projected output.

* Electricity and district heat: read from the JRC/GTAP allocation written by
  application.main(), <code>2023_AEA_D35_GTAP_emissions.csv (index
  GTAP_activity; CO2_tonnes, nonCO2_tCO2e, GHG_tCO2e). These become the rows
  CO2 / nonCO2 / GHG of the technology columns.
* All other sectors (optional): --emissions_file, one CSV per country named
  <stem>_<EST|LVA|LTU>.csv, rows CO2 / nonCO2 / GHG in the same units (tonnes),
  columns = IO sector codes. Any aggregate "D" column is ignored.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from projection import project_and_rebalance, project_emissions, table_layout
from price_model import run_price_scenarios

D35_ROWS = {"CO2": "CO2_tonnes", "nonCO2": "nonCO2_tCO2e", "GHG": "GHG_tCO2e"}


def _mode_and_blend(key: str, ras_sensitivities: dict):
    """Keys are '<alternative>_<mode>_<sensitivity>', e.g. GTAP_full_sensitivity_10pct."""
    _, mode, sensitivity = key.split("_", 2)
    return mode, ras_sensitivities[sensitivity]


def load_d35_extension(path: Path) -> pd.DataFrame:
    """Electricity / heat emissions by GTAP activity from the JRC allocation CSV."""
    frame = pd.read_csv(path, index_col="GTAP_activity")
    return pd.DataFrame({row: frame[col] for row, col in D35_ROWS.items()}).T


def build_extension(table: pd.DataFrame, d35_path: Path | None, other_path: Path | None):
    """Combine the D35 allocation and the optional other-sector file for one table."""
    techs, industry_cols, _ = table_layout(table)
    parts = []

    if other_path is not None and Path(other_path).exists():
        other = pd.read_csv(other_path, index_col=0).drop(columns=["D"], errors="ignore")
        parts.append(other[[c for c in other.columns if c not in techs]])

    if d35_path is not None and Path(d35_path).exists():
        d35 = load_d35_extension(d35_path)
        lost = [c for c in d35.columns if c not in techs and d35[c].abs().sum() > 0]
        if lost:
            print(f"WARNING: emissions allocated to {lost} are dropped because those "
                  "activities are not columns of the table (drop_inactive).")
        parts.append(d35[[c for c in d35.columns if c in techs]])

    if not parts:
        return None
    rows = parts[0].index
    for p in parts[1:]:
        if set(p.index) != set(rows):
            raise ValueError(
                f"Emission row labels differ between inputs: {sorted(rows)} vs {sorted(p.index)}."
            )
    ext = pd.concat([p.reindex(rows) for p in parts], axis=1)
    return ext[[c for c in ext.columns if c in industry_cols]]


def run_projection(rebalanced_data, country_codes, year, output_dir, ras_balance,
                   ras_sensitivities, household_column="HFCE", emissions_file=None):
    """
    rebalanced_data : {country: {key: balanced 2022 table}} from application.main()
    country_codes   : {country: 'EST'|'LVA'|'LTU'}
    ras_balance     : the project's _ras_balance (ras_core.py)
    emissions_file  : optional other-sector extension, see module docstring
    """
    output_dir = Path(output_dir)
    projected_data = {}

    for country, alternatives in rebalanced_data.items():
        code = country_codes[country]
        projected_data[country] = {}
        d35_path = output_dir / f"{code}2023_AEA_D35_GTAP_emissions.csv"
        other_path = None
        if emissions_file:
            stem = Path(emissions_file)
            other_path = stem.with_name(f"{stem.stem}_{code}{stem.suffix}")

        for key, matrix in alternatives.items():
            mode, blend = _mode_and_blend(key, ras_sensitivities)
            label = f"{country} - {key} - 2030 BAU"
            print(f"\n{'=' * 70}\n{label}: projection and RAS\n{'=' * 70}")

            projected, _ = project_and_rebalance(
                matrix, label, code, ras_balance=ras_balance, mode=mode, blend=blend,
            )
            projected_data[country][key] = projected
            projected.to_csv(output_dir / f"{code}{year}_{key.lower()}_disaggregated_2030BAU.csv")

            # The extension and price model must not discard the projection if they fail.
            try:
                extension = build_extension(matrix, d35_path, other_path)
                if extension is None:
                    print(f"{label}: no emissions inputs found; emissions not projected.")
                else:
                    emissions_2030, _ = project_emissions(extension, matrix, projected)
                    extension.to_csv(output_dir / f"{code}{year}_{key.lower()}_emissions_2022.csv")
                    emissions_2030.to_csv(
                        output_dir / f"{code}{year}_{key.lower()}_emissions_2030BAU.csv"
                    )
            except Exception as exc:                          # noqa: BLE001
                print(f"{label}: WARNING emissions projection skipped: {exc!r}")

            for tag, table in (("2022", matrix), ("2030BAU", projected)):
                try:
                    _, summary = run_price_scenarios(table, code, hh_col=household_column)
                    summary.to_csv(output_dir / f"{code}_{key.lower()}_price_model_{tag}.csv")
                except Exception as exc:                      # noqa: BLE001
                    print(f"{label}: WARNING price model ({tag}) skipped: {exc!r}")

    return projected_data
