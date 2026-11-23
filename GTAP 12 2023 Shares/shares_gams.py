import pandas as pd
import numpy as np
import sys
from argparse import ArgumentParser
import gams.transfer as gt
from pathlib import Path
from projection import project_and_rebalance, project_emissions
from price_model import run_price_scenarios
from ras_core import _ras_balance

from mapping import GTAP_TO_OECD, OECD_NAMES


ACTIVITIES = ["GDT",
              "TnD",
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
              "SolarP"]

GENERATING_TECHS = ["TnD",
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
              "SolarP"]

ROW_TECHS = GENERATING_TECHS + ["GDT"]

# GTAP ENDW endowment names.
FACTOR_INPUTS = [
    "tech_aspros",
    "clerks",
    "service_shop",
    "off_mgr_pros",
    "ag_othlowsk",
    "Capital",
    "NatlRes",
    "Land",
]

COUNTRY_NAMES = {"LVA": "Latvia", "LTU": "Lithuania", "EST": "Estonia"}

def parse_args(args):
    parser = ArgumentParser(add_help=True)
    parser.add_argument(
        "--shares_database",
        default=r"..\GDX12aPower23\GSDFDAT.GDX",
        help=r"The GTAP GDX database file path. Defaults to ..\GDX12aPower23\GSDFDAT.GDX if not specified",
    )
    parser.add_argument(
        "--country_columns",
        default="LVA LTU EST",
        help="The country column names, in REGION_CODES order. Defaults to 'LVA LTU EST' if not specified",
    )
    parser.add_argument(
        "--io_database_root",
        default=r"D:\Research\Baltic Paper Daria\Carl - Baltic states methodology",
        help="The root directory of the OECD IO database. Defaults to D:\\Research\\Baltic Paper Daria\\Carl - Baltic states methodology if not specified",
    )
    parser.add_argument("--year", default="2022", help="The year of the IO tables. Defaults to 2022 if not specified")

    parsed_arguments = parser.parse_args(args=args)

    return parsed_arguments

def pivot_parameter(records, activity, columns, parsed_arguments):
    # Filter a GTAP parameter (dim 1 x ACTS x REG) to one activity and the Baltic
    # regions, then pivot from long records to a dim-1 x country wide table.
    row_dim = records.columns[0]
    sub = records[(records["ACTS"] == activity) & (records["REG"].isin(parsed_arguments.country_columns.split()))]
    wide = sub.pivot_table(index=row_dim, columns="REG", values="value",
                           aggfunc="sum", observed=True)
    wide = wide.reindex(columns=parsed_arguments.country_columns.split()).fillna(0.0)
    wide.columns = columns
    wide.index = wide.index.astype(str)
    return wide

def data_in(file_path, parsed_args):
    c = gt.Container(file_path)

    # VDFB(COMM, ACTS, REG): domestic intermediate purchases.
    vdfb = c["VDFB"].records.copy()

    # EVFB(ENDW, ACTS, REG): primary factor purchases.
    evfb = c["EVFB"].records.copy()

    # VMFB(COMM, ACTS, REG): imported intermediate purchases.
    vmfb = c["VMFB"].records.copy()

    country_columns = parsed_args.country_columns.split()
    data = {}

    for activity in ACTIVITIES:
        intermediate = pivot_parameter(
            vdfb, activity, country_columns, parsed_args
        )
        factor = pivot_parameter(
            evfb, activity, country_columns, parsed_args
        )

        frame = pd.concat([intermediate, factor])
        frame.index.name = "VDFB"
        data[activity] = frame.reset_index()

    return data, vdfb, vmfb

def shares(data, parsed_args):
    factor_inputs = FACTOR_INPUTS
    df_shares = {}
    for technology, frame in data.items():
        result = frame.set_index("VDFB").copy()
        if (result.to_numpy() == 0).all():
            print(f"{technology}: no activity in any region; skipping")
            continue
        factor_rows = result.index.isin(factor_inputs)
        # Divide intermediate and factor blocks by their respective column sums.
        for rows in [~factor_rows, factor_rows]:
            totals = result.loc[rows].sum(axis=0, skipna=False)
            # Leave NaN shares for regions where the activity does not exist.
            totals = totals.where(totals != 0)
            result.loc[rows] = result.loc[rows].div(totals, axis="columns")
        df_shares[technology] = result.reset_index()
    return df_shares


def shares_over_technologies(records, parsed_args, aggregate=False, weights=None):
    """
    Calculate the share of each generating technology in each purchasing
    sector's total purchases of generating-technology commodities.

    Parameters
    ----------
    records : pd.DataFrame
        Raw VDFB(COMM, ACTS, REG) records.

    parsed_args : argparse.Namespace
        Parsed command-line arguments.

    aggregate : bool
        If True, aggregate purchasing activities to OECD sectors before
        calculating shares.

    Returns
    -------
    dict
        Generating technology -> DataFrame of purchasing-sector shares
        by country.
    """

    countries = parsed_args.country_columns.split()
    # Standardize the GDX value column.
    if "value" in records.columns and "VALUE" not in records.columns:
        records = records.rename(columns={"value": "VALUE"})

    # Keep purchases of generating technologies by other activities.
    sub = records[
        records["COMM"].isin(ROW_TECHS)
        & records["REG"].isin(countries)
    ].copy()
    

    # Optionally map purchasing activities to OECD sectors.
    if aggregate:
        if weights is None:
            raise ValueError("aggregate=True requires output weights")

        unmapped = set(sub["ACTS"]) - set(GTAP_TO_OECD)

        if unmapped:
            raise KeyError(
                "Purchasing activities without an OECD mapping: "
                f"{sorted(unmapped)}"
            )

        sub["SECTOR"] = sub["ACTS"].astype(object).map(GTAP_TO_OECD)
        sub = sub[sub["SECTOR"].notna()].copy()

        # Record original totals before allocating activities.
        original_totals = sub.groupby("REG")["VALUE"].sum()

        # Convert scalar mappings into lists.
        sub["SECTOR"] = sub["SECTOR"].apply(
            lambda x: x if isinstance(x, list) else [x]
        )

        # Expand one-to-many mappings.
        sub = sub.explode("SECTOR")
        
        # Country-specific output-share weights; 1.0 for one:one mappings.
        sub["WEIGHT"] = [
            weights[reg][acts][sector] if acts in weights[reg] else 1.0
            for acts, sector, reg in zip(
                sub["ACTS"].astype(str), sub["SECTOR"], sub["REG"].astype(str)
            )
        ]

        # Check that allocation weights sum to one for each original
        # GTAP observation.
        weight_sums = sub.groupby(
            ["ACTS", "COMM", "REG"]
        )["WEIGHT"].sum()

        if not np.allclose(weight_sums.to_numpy(), 1.0):
            raise ValueError(
                "OECD activity allocation weights do not sum to one:\n"
                f"{weight_sums[~np.isclose(weight_sums, 1.0)]}"
            )

        # Apply allocation weights to the original values.
        sub["VALUE"] = sub["VALUE"] * sub["WEIGHT"]

        # Check that allocation preserves total purchases by country.
        allocated_totals = sub.groupby("REG")["VALUE"].sum()

        if not np.allclose(
            original_totals.reindex(countries).to_numpy(),
            allocated_totals.reindex(countries).fillna(0.0).to_numpy(),
            rtol=1e-9,
            atol=1e-10
        ):
            raise ValueError(
                "OECD activity allocation changed total purchases.\n"
                f"Original totals:\n{original_totals}\n"
                f"Allocated totals:\n{allocated_totals}"
            )

        # Construct the OECD sector ordering, including list-valued mappings.
        sector_order = list(dict.fromkeys(
            sector
            for value in GTAP_TO_OECD.values()
            if value is not None
            for sector in (value if isinstance(value, list) else [value])
        ))

        sector_name = "OECD"

    else:
        sub["SECTOR"] = sub["ACTS"]
        sector_order = list(dict.fromkeys(sub["SECTOR"]))
        sector_name = "ACTS"

    # Aggregate purchases by technology, purchasing sector and country.
    grouped = (
        sub.groupby(
            ["COMM", "SECTOR", "REG"],
            observed=True
        )["VALUE"]
        .sum()
        .reset_index()
    )

    frames = {}

    for technology in ROW_TECHS:

        technology_data = grouped[
            grouped["COMM"] == technology
        ]

        if technology_data.empty:
            frame = pd.DataFrame(
                0.0,
                index=sector_order,
                columns=countries
            )

        else:
            frame = technology_data.pivot_table(
                index="SECTOR",
                columns="REG",
                values="VALUE",
                aggfunc="sum",
                observed=True
            )

            frame = frame.reindex(
                index=sector_order,
                columns=countries
            ).fillna(0.0)

        frames[technology] = frame

    # Sum purchases across generating technologies for each sector and country.
    totals = sum(frames.values())

    # Check that technology shares sum to one wherever purchases are positive.
    valid_totals = totals.where(totals != 0)

    result = {
        technology: (
            frame.div(valid_totals, axis=1)
            .rename_axis(sector_name)
            .reset_index()
        )
        for technology, frame in frames.items()
    }

    # Validate that technology shares sum to one.
    share_totals = sum(
        frame.div(valid_totals, axis=1)
        for frame in frames.values()
    )

    valid_shares = totals != 0

    deviations = (share_totals - 1.0).where(valid_shares)

    failed = deviations.abs() > 1e-9

    if failed.any().any():
        print("\nTechnology share validation failed.")

        print("\nPurchases by generating technology:")
        print(totals.loc[failed.any(axis=1)])

        print("\nCalculated technology shares:")
        for technology, frame in frames.items():
            print(f"\n{technology}:")
            print(frame.loc[failed.any(axis=1)])

        print("\nSum of technology shares:")
        print(share_totals.loc[failed.any(axis=1)])

        print("\nDeviations from one:")
        print(deviations.loc[failed.any(axis=1)])

        raise ValueError(
            "Generating technology shares do not sum to one."
        )

    return result


def load_output_weights(parsed_args):
    """
    Country-specific output-share weights for every one:many entry in
    GTAP_TO_OECD, taken from the OUTPUT row of each country's OECD IO table.

    Returns {country_code: {gtap_code: pd.Series(weights by OECD sector)}}.
    """
    root = Path(parsed_args.io_database_root)
    weights = {}

    for code in parsed_args.country_columns.split():
        path = (
            root / COUNTRY_NAMES[code] / "1. OECD Tables"
            / "2. Domestic output & imports" / f"{code}{parsed_args.year}dom.csv"
        )
        io = pd.read_csv(path, index_col=0)
        output = io.loc["OUTPUT"].astype(float)

        country_weights = {}
        for gtap, targets in GTAP_TO_OECD.items():
            if not isinstance(targets, list):
                continue

            missing = [s for s in targets if s not in output.index]
            if missing:
                raise KeyError(f"{code} {gtap}: sectors missing from IO table: {missing}")

            out = output[targets]
            if out.sum() > 0:
                w = out / out.sum()
            else:
                print(f"WARNING {code} {gtap}: zero output in {targets}; using equal weights")
                w = pd.Series(1.0 / len(targets), index=targets)

            country_weights[gtap] = w
            print(f"{code} {gtap}: {w.round(4).to_dict()}")

        weights[code] = country_weights

    return weights

def aggregate_to_oecd(data, weights):
    # Aggregate GTAP commodity rows into OECD IO sectors.
    # One-to-many mappings are allocated by each country's output shares.
    oecd_order = list(dict.fromkeys(
        oecd
        for value in GTAP_TO_OECD.values()
        if value is not None
        for oecd in (value if isinstance(value, list) else [value])
    ))

    aggregated = {}

    for technology, frame in data.items():
        result = frame.set_index("VDFB")

        factor_rows = result.index.isin(FACTOR_INPUTS)
        intermediate = result.loc[~factor_rows].copy()

        unmapped = set(intermediate.index) - set(GTAP_TO_OECD)
        if unmapped:
            raise KeyError(
                f"{technology}: GTAP commodities without an OECD mapping: "
                f"{sorted(unmapped)}"
            )

        intermediate = intermediate[
            intermediate.index.map(GTAP_TO_OECD).notna()
        ].copy()

        value_columns = intermediate.columns.tolist()   # country codes
        original_totals = intermediate[value_columns].sum()

        agg = pd.DataFrame(0.0, index=oecd_order, columns=value_columns)

        for gtap, values in intermediate.iterrows():
            vals = values[value_columns].to_numpy(dtype=float)
            target = GTAP_TO_OECD[gtap]
            if isinstance(target, list):
                for sector in target:
                    w = np.array([weights[c][gtap][sector] for c in value_columns])
                    agg.loc[sector] += vals * w
            else:
                agg.loc[target] += vals

        intermediate = agg.rename_axis("VDFB")

        aggregated_totals = intermediate[value_columns].sum()
        if not np.allclose(
            original_totals.to_numpy(), aggregated_totals.to_numpy(),
            rtol=1e-9, atol=1e-10
        ):
            raise ValueError(
                f"{technology}: Aggregation changed intermediate input totals.\n"
                f"Original totals:\n{original_totals}\n"
                f"Aggregated totals:\n{aggregated_totals}"
            )

        frame_out = pd.concat([intermediate, result.loc[factor_rows]])
        frame_out.index.name = "VDFB"
        aggregated[technology] = frame_out.reset_index()

    return aggregated

def label_oecd(df_shares):
    # Replace the VDFB label column with the OECD code and full sector name.
    renamed = {}
    for technology, frame in df_shares.items():
        frame = frame.rename(columns={"VDFB": "OECD"})
        frame.insert(1, "Sector", frame["OECD"].map(OECD_NAMES))
        renamed[technology] = frame
    return renamed

def shares_oecd(data, parsed_args, weights):
    return label_oecd(shares(aggregate_to_oecd(data, weights), parsed_args))

def shares_oecd_over_technologies(records, parsed_args, weights):
    return label_oecd(
        shares_over_technologies(records, parsed_args, aggregate=True, weights=weights)
    )

def main(args=None):
    parsed_args = parse_args(args)

    file_path = parsed_args.shares_database

    # Read domestic and imported GTAP intermediate purchases.
    data, vdfb, vmfb = data_in(file_path, parsed_args)

    # Country-specific output weights for GTAP-to-OECD aggregation.
    weights = load_output_weights(parsed_args)

    # ---------------------------------------------------------
    # Domestic input composition and domestic electricity shares
    # ---------------------------------------------------------

    # Input composition of each generating technology.
    result = shares(data, parsed_args)

    # Domestic technology allocation across GTAP purchasing activities.
    result_row = shares_over_technologies(vdfb, parsed_args)

    # OECD-level domestic input levels.
    oecd_levels = aggregate_to_oecd(data, weights)
    result_oecd = label_oecd(shares(oecd_levels, parsed_args))

    # Domestic technology allocation across OECD purchasing sectors.
    result_oecd_row = shares_oecd_over_technologies(
        vdfb,
        parsed_args,
        weights
    )

    # ---------------------------------------------------------
    # NEW: Import allocation using GTAP VMFB
    # ---------------------------------------------------------

    # Import technology allocation across GTAP purchasing activities.
    result_import_row = shares_over_technologies(
        vmfb,
        parsed_args
    )

    # Import technology allocation across OECD purchasing sectors.
    # This uses the same country-specific GTAP-to-OECD output weights
    # as the domestic allocation above.
    result_import_oecd_row = shares_oecd_over_technologies(
        vmfb,
        parsed_args,
        weights
    )

    # ---------------------------------------------------------
    # Export diagnostics
    # ---------------------------------------------------------

    with pd.ExcelWriter("input_shares_gams.xlsx") as writer:
        for sheet_name, df in result.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)

    with pd.ExcelWriter("input_shares_row_gams.xlsx") as writer:
        for sheet_name, df in result_row.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)

    with pd.ExcelWriter("input_shares_oecd_gams.xlsx") as writer:
        for sheet_name, df in result_oecd.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)

    with pd.ExcelWriter("input_shares_oecd_row_gams.xlsx") as writer:
        for sheet_name, df in result_oecd_row.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)

    # NEW: Export the VMFB-based import allocation shares.
    with pd.ExcelWriter("import_shares_row_gams.xlsx") as writer:
        for sheet_name, df in result_import_row.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)

    with pd.ExcelWriter("import_shares_oecd_row_gams.xlsx") as writer:
        for sheet_name, df in result_import_oecd_row.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)

    return (
        result,
        result_row,
        result_oecd,
        result_oecd_row,
        oecd_levels,
        data,
        result_import_row,
        result_import_oecd_row,
    )


if __name__ == "__main__":
    main(sys.argv[1:])