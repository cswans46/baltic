import sys
from argparse import ArgumentParser
from pathlib import Path
import matplotlib
matplotlib.use("Agg")   # write PNG files without opening a window
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from import_shares import (
    load_country_eia_workbook,
    create_import_alternatives,
    save_import_alternatives,
)

from shares_gams import ACTIVITIES, FACTOR_INPUTS, main as calculate_shares
from jrc_allocation import (
    allocate_oecd_aea_emissions,
    build_jrc_output_shares,
    find_country_workbook,
    load_gtap_emissions_weights,
    GTAP_GENERATING_ACTIVITIES,
)

COUNTRY_CODES = {
    "Latvia": "LVA",
    "Lithuania": "LTU",
    "Estonia": "EST",
}

# Every GTAP electricity activity (incl. GDT) becomes its own IO sector.
# New IO labels: rows DOM_<tech>, columns <tech>.


def parse_args(args=None):
    parser = ArgumentParser(add_help=True)

    parser.add_argument(
        "--io_database_root",
        default=r"D:\Research\Baltic Paper Daria\Carl - Baltic states methodology",
        help="Root directory containing the country folders.",
    )
    parser.add_argument("--year", default="2022", help="Year of the OECD IO tables.")
    parser.add_argument(
        "--countries_to_share",
        default="Latvia Lithuania Estonia",
        help="Countries to disaggregate.",
    )
    parser.add_argument(
        "--drop_inactive",
        action="store_true",
        help="Omit technologies with no activity in a country (default: keep all 13 "
             "as labels, with all-zero rows/columns where inactive).",
    )
    parser.add_argument(
        "--jrc_powergen_pattern",
        default="*PowerGen*.xlsx",
        help="Filename pattern for the country-specific JRC-IDEES PowerGen workbook.",
    )
    parser.add_argument(
        "--aea_emissions_pattern",
        default="OECD {country} 2022 emissions.xlsx",
        help="Filename pattern for the country-specific OECD AEA workbook.",
    )
    parser.add_argument(
        "--co2_gdx",
        default=r"..\GDX12aPower23\GSDFEMISS.GDX",
        help="GTAP CO2 emissions GDX file.",
    )
    parser.add_argument(
        "--nonco2_gdx",
        default=r"..\GDX12aPower23\GSDFNCO2.GDX",
        help="GTAP non-CO2 emissions GDX file.",
    )
    parser.add_argument(
        "--gams_system_directory",
        default=r"C:\GAMS\51",
        help="GAMS system directory used to read GTAP GDX files.",
    )

    return parser.parse_args(args=args)


def data_in(parsed_args):
    data = {}
    root = Path(parsed_args.io_database_root)

    for country in parsed_args.countries_to_share.split():
        code = COUNTRY_CODES[country]
        file_path = (
            root / country / "1. OECD Tables" / "2. Domestic output & imports"
            / f"{code}{parsed_args.year}dom.csv"
        )
        if not file_path.exists():
            raise FileNotFoundError(f"Could not find IO database for {country}: {file_path}")
        data[country] = {"DOM_D": pd.read_csv(file_path, index_col=0)}

    return data

def format_vmfb_import_shares(import_oecd_row_frames, country_code):
    """
    Convert the dictionary returned by shares_oecd_over_technologies()
    into a technology x OECD purchasing-sector DataFrame.

    Rows:    GTAP electricity activities
    Columns: OECD purchasing sectors
    Values:  VMFB-based import allocation shares
    """
    missing = set(ACTIVITIES) - set(import_oecd_row_frames)

    if missing:
        raise KeyError(
            f"Missing VMFB import-share results for technologies: {sorted(missing)}"
        )

    shares = {}

    for technology in ACTIVITIES:
        frame = import_oecd_row_frames[technology]

        if country_code not in frame.columns:
            raise KeyError(
                f"{technology}: country {country_code} missing from "
                "OECD import-share results."
            )

        if "OECD" not in frame.columns:
            raise KeyError(
                f"{technology}: OECD sector identifier not found."
            )

        shares[technology] = (
            frame.set_index("OECD")[country_code]
            .astype(float)
        )

    result = pd.DataFrame.from_dict(shares, orient="index")
    result = result.reindex(index=ACTIVITIES).fillna(0.0)

    # Validate the technology shares for each purchasing sector.
    totals = result.sum(axis=0)
    valid = totals > 0

    if not np.allclose(
        totals.loc[valid].to_numpy(),
        1.0,
        rtol=1e-9,
        atol=1e-9
    ):
        raise ValueError(
            f"{country_code}: VMFB import technology shares do not sum to one."
        )

    return result

def add_import_alternatives(
    io_domestic_disaggregated,
    country,
    country_code,
    year,
    root,
    output_folder,
    vmfb_import_shares,
    purchaser_to_eia=None,
):
    """
    Add the available import-allocation alternatives to an already
    domestically disaggregated IO table.

    Latvia: GTAP/VMFB only.
    Lithuania and Estonia: EIA and GTAP/VMFB.
    """
    if country == "Latvia":
        print(
            f"{country}: no EIA workbook will be loaded; "
            "using the GTAP/VMFB import allocation only."
        )
        eia_weights = None
        purchaser_mapping = None
    else:
        eia_weights = load_country_eia_workbook(
            root=root,
            country=country,
        )
        purchaser_mapping = purchaser_to_eia
        
    print("\n--- VMFB IMPORT SHARE DIAGNOSTICS ---")
    print("IO purchasing columns:")
    print(list(io_domestic_disaggregated.columns))

    print("\nVMFB share columns:")
    print(list(vmfb_import_shares.columns))

    print("\nMatching columns:")
    print(
        sorted(
            set(io_domestic_disaggregated.columns)
            & set(vmfb_import_shares.columns)
        )
    )

    print("\nVMFB share totals:")
    print(vmfb_import_shares.sum(axis=0).to_string())
    print("------------------------------------\n")

    io_eia, io_gtap = create_import_alternatives(
        io=io_domestic_disaggregated,
        eia_weights=eia_weights,
        vmfb_shares=vmfb_import_shares,
        purchaser_to_eia=purchaser_mapping,
        source_row="IMP_D",
        total_column="TOTAL",
    )

    paths = save_import_alternatives(
        io_eia=io_eia,
        io_gtap=io_gtap,
        output_folder=output_folder,
        country_code=country_code,
        year=year,
    )

    print(f"{country}: saved import alternatives:")
    for method, path in paths.items():
        print(f"  {method}: {path}")

    return io_eia, io_gtap

def disaggregate_electricity(
    io, country, row_frames, level_frames, raw_frames,
    drop_inactive=False, jrc_output_shares=None,
):
    """
    Split aggregate electricity (DOM_D row, D column) into one sector per
    GTAP electricity activity.

    row_frames   : OECD row shares   {tech: sector x country shares}
    level_frames : OECD input levels {tech: input  x country levels}
    raw_frames   : raw GTAP levels   {tech: GTAP commodity x country levels}
    """
    # ---- Inputs, all indexed by technology -------------------------------
    # R: tech x purchasing sector, share of each sector's electricity bought
    #    from each technology.
    R = pd.DataFrame({t: row_frames[t].set_index("OECD")[country]
                      for t in ACTIVITIES}).T
    # L: OECD input sector x tech (levels).
    L = pd.DataFrame({t: level_frames[t].set_index("VDFB")[country]
                      for t in ACTIVITIES}).fillna(0.0)
    # E: electricity commodity i (rows) bought by activity j (columns), from
    #    the raw GTAP data so that no technology is grouped.
    E = pd.DataFrame({
        t: raw_frames[t].set_index("VDFB")[country].reindex(ACTIVITIES).fillna(0.0)
        for t in ACTIVITIES
    })

    # Fallback column weights: each tech's share of total intermediate inputs.
    tot = L.loc[~L.index.isin(FACTOR_INPUTS)].sum()
    # Non-electricity input rows only (the D_* rows in L are grouped; E replaces them).
    L = L.loc[[i for i in L.index if not str(i).startswith("D_")]]

    dom = io.loc["DOM_D"]
    sector_cols = [c for c in io.columns if c != "D" and f"DOM_{c}" in io.index]
    fd_cols = [c for c in io.columns
               if c not in sector_cols and c not in ("D", "TOTAL")]

    # ---- Supply-side mix ----------------------------------------------------
    R = R.reindex(columns=sector_cols)

    # Preserve the GTAP split between electricity generation and the
    # non-generating activities (T&D and gas/steam distribution), but use
    # JRC-IDEES 2023 net generation shares to split the generating portion
    # across the 11 GTAP generation technologies.
    if jrc_output_shares is not None:
        jrc_mix = pd.Series(0.0, index=GTAP_GENERATING_ACTIVITIES, dtype=float)
        jrc_mix.loc[jrc_output_shares.index] = jrc_output_shares.astype(float)
        if (jrc_mix < 0).any() or not np.isclose(jrc_mix.sum(), 1.0, atol=1e-9):
            raise ValueError(f"{country}: JRC generation shares must be non-negative and sum to one.")
        generation_total = R.loc[GTAP_GENERATING_ACTIVITIES].sum(axis=0)
        R.loc[GTAP_GENERATING_ACTIVITIES, :] = np.outer(
            jrc_mix.to_numpy(),
            generation_total.to_numpy(),
        )

    mix = R.mul(dom[sector_cols], axis=1).sum(axis=1)
    mix = mix / mix.sum()                       # economy-wide technology mix

    # ---- Which technologies to keep ------------------------------------------
    active = (L.sum() + E.sum() > 0) | (mix > 0)
    techs = [t for t in ACTIVITIES if active[t] or not drop_inactive]
    inactive = [t for t in ACTIVITIES if not active[t]]
    print(f"{country}: technologies with no activity: {inactive} "
          f"({'dropped' if drop_inactive else 'kept as zero rows/columns'})")

    R, L, E = R.loc[techs], L[techs], E.loc[techs, techs]
    mix = mix[techs] / mix[techs].sum()
    fallback = tot[techs] / tot[techs].sum()

    # Sectors without a share (no GTAP electricity purchases) get the mix.
    no_share = list(R.columns[R.isna().any()])
    if no_share:
        print(f"{country}: using economy-wide mix for sectors: {no_share}")    for c in no_share:
        R[c] = mix

    def col_weights(code):
        """Share of technology t in the purchases of input `code` (sums to 1)."""
        if code == "D":
            v = E.sum(axis=0)                   # electricity bought by each tech
        else:
            v = L.reindex([code]).iloc[0]
        v = v.fillna(0.0)
        return v / v.sum() if v.sum() > 0 else fallback

    # ---- Output frame with D replaced in place --------------------------------
    new_rows = [f"DOM_{t}" for t in techs]
    base = io.drop(index="DOM_D", columns="D")
    new_index, new_columns = [], []
    for r in io.index:
        new_index += new_rows if r == "DOM_D" else [r]
    for c in io.columns:
        new_columns += techs if c == "D" else [c]
    out = base.reindex(index=new_index, columns=new_columns)

    # Supply row: sector columns and final demand.
    out.loc[new_rows, sector_cols] = R.mul(dom[sector_cols], axis=1).to_numpy()
    out.loc[new_rows, fd_cols] = np.outer(mix.to_numpy(), dom[fd_cols].to_numpy())

    # Electricity-to-electricity block from raw GTAP flows (supplier x buyer).
    M = E.to_numpy()
    M = M / M.sum() if M.sum() > 0 else np.outer(mix, col_weights("D"))
    out.loc[new_rows, techs] = io.loc["DOM_D", "D"] * M

    # Demand side: new technology columns.
    # Imported inputs, import-related taxes and TXS are NOT yet shared out with GTAP data.
    # Placeholder: split by each technology's share of electricity sales.
    # Replace `pending` with GTAP import-based weights when ready.
    pending = mix

    for r in base.index:
        if r in ("TTL_INT_FNL", "OUTPUT"):
            continue
        if r.startswith("DOM_"):
            w = col_weights(r.split("_", 1)[1])
        elif r == "VALU":
            v = L.loc[L.index.isin(FACTOR_INPUTS)].sum()
            w = v / v.sum() if v.sum() > 0 else fallback
        else:                                    # IMP_*, IMP_OTHER, TXS_INT_FNL
            w = pending
        out.loc[r, techs] = (io.loc[r, "D"] * w).to_numpy()

    # Totals for the new columns / rows.
    parts = [r for r in out.index if r not in ("TTL_INT_FNL", "OUTPUT", "VALU")]
    out.loc["TTL_INT_FNL", techs] = out.loc[parts, techs].sum().to_numpy()
    out.loc["OUTPUT", techs] = (
        out.loc["TTL_INT_FNL", techs] + out.loc["VALU", techs]
    ).to_numpy()
    out.loc[new_rows, "TOTAL"] = (
        out.loc[new_rows].drop(columns="TOTAL").sum(axis=1).to_numpy()
    )

    # ---- Conservation checks ----------------------------------------------------
    def ok(a, b):
        return np.allclose(a, b, rtol=1e-6, atol=1e-6)

    cols = [c for c in io.columns if c not in ("D", "TOTAL")]
    assert out.isna().sum().sum() == 0, f"{country}: NaNs left in disaggregated table"
    assert ok(out.loc[new_rows, cols].sum().to_numpy(), dom[cols].to_numpy()), \
        f"{country}: DOM_D supply row not conserved"
    chk = [r for r in base.index if r not in ("TTL_INT_FNL", "OUTPUT")]
    assert ok(out.loc[chk, techs].sum(axis=1).to_numpy(), io.loc[chk, "D"].to_numpy()), \
        f"{country}: D column not conserved"
    assert ok(out.loc[new_rows, techs].to_numpy().sum(), io.loc["DOM_D", "D"]), \
        f"{country}: D-to-D block not conserved"
    # Published totals are rounded to 3 dp, so compare with a rounding-sized tolerance.
    parts_orig = [r for r in io.index if r not in ("TTL_INT_FNL", "OUTPUT", "VALU")]
    resid_ttl = io.loc["TTL_INT_FNL", "D"] - io.loc[parts_orig, "D"].sum()
    resid_out = io.loc["OUTPUT", "D"] - (io.loc["TTL_INT_FNL", "D"] + io.loc["VALU", "D"])
    print(f"{country}: source-table rounding residuals for D: "
          f"TTL_INT_FNL {resid_ttl:.4f}, OUTPUT {resid_out:.4f}")

    tol = 0.05   # about 100 cells rounded to 0.0005 each, worst case
    assert abs(out.loc["TTL_INT_FNL", techs].sum() - io.loc["TTL_INT_FNL", "D"]) < tol, \
        f"{country}: TTL_INT_FNL of D not conserved"
    assert abs(out.loc["OUTPUT", techs].sum() - io.loc["OUTPUT", "D"]) < tol, \
        f"{country}: OUTPUT of D not conserved"
    print(f"{country}: checks passed. OUTPUT(D) original "
          f"{io.loc['OUTPUT', 'D']:.3f} vs new {out.loc['OUTPUT', techs].sum():.3f}")

    return out

RAS_SENSITIVITIES = {
    "baseline": 0.00,
    "sensitivity_10pct": 0.10,
    "sensitivity_25pct": 0.25,
}


def _ras_balance(seed, row_targets, col_targets, country,
                 mode, blend=0.0, max_iter=5000, tol=1e-9):
    """
    Balance a signed IO matrix.

    Negative transactions are retained at their original values.
    The nonnegative transactions are balanced to the residual row
    and column targets.

    blend controls the proportional seed sensitivity:
        0.00 = original allocation
        0.10 = 10% proportional seed
        0.25 = 25% proportional seed

    Negative cells are excluded from the proportional seed.
    """
    seed = np.asarray(seed, dtype=float).copy()
    row_targets = np.asarray(row_targets, dtype=float).copy()
    col_targets = np.asarray(col_targets, dtype=float).copy()

    if not 0.0 <= blend <= 1.0:
        raise ValueError(
            f"{country}: RAS blend must be between 0 and 1; "
            f"got {blend}."
        )

    if not np.all(np.isfinite(seed)):
        raise ValueError(f"{country}: RAS seed contains non-finite values.")

    if not np.all(np.isfinite(row_targets)) or not np.all(
        np.isfinite(col_targets)
    ):
        raise ValueError(f"{country}: RAS targets contain non-finite values.")

    # ---------------------------------------------------------
    # 1. Reconcile the original row and column grand totals.
    # ---------------------------------------------------------
    row_total = row_targets.sum()
    col_total = col_targets.sum()

    if row_total <= 0 or col_total <= 0:
        raise ValueError(
            f"{country}: {mode} RAS has a non-positive grand total."
        )

    col_targets *= row_total / col_total

    # ---------------------------------------------------------
    # 2. Separate negative and nonnegative transactions.
    # ---------------------------------------------------------
    negative = np.minimum(seed, 0.0)
    positive_seed = np.maximum(seed, 0.0)

    negative_row_totals = negative.sum(axis=1)
    negative_col_totals = negative.sum(axis=0)

    # Residual targets for the nonnegative part of the matrix.
    positive_row_targets = row_targets - negative_row_totals
    positive_col_targets = col_targets - negative_col_totals

    if np.any(positive_row_targets < -tol):
        raise ValueError(
            f"{country}: {mode} RAS has negative residual row targets "
            "after retaining negative transactions."
        )

    if np.any(positive_col_targets < -tol):
        raise ValueError(
            f"{country}: {mode} RAS has negative residual column targets "
            "after retaining negative transactions."
        )

    positive_row_targets = np.maximum(positive_row_targets, 0.0)
    positive_col_targets = np.maximum(positive_col_targets, 0.0)

    positive_total = positive_row_targets.sum()

    if not np.isclose(
        positive_total,
        positive_col_targets.sum(),
        rtol=1e-9,
        atol=tol,
    ):
        raise ValueError(
            f"{country}: {mode} RAS residual row and column totals "
            "do not agree."
        )

    if positive_total <= 0:
        if np.allclose(positive_row_targets, 0.0, atol=tol) and np.allclose(
            positive_col_targets, 0.0, atol=tol
        ):
            return negative, {
                "Row margins": [0.0],
                "Column margins": [0.0],
            }, 0, 0.0

        raise ValueError(
            f"{country}: {mode} RAS has no positive transactions to balance."
        )

    # ---------------------------------------------------------
    # 3. Construct the proportional seed sensitivity.
    # ---------------------------------------------------------
    # Do not introduce positive transactions into cells that
    # originally contained negative transactions.
    allowed = seed >= 0.0

    proportional_seed = (
        np.outer(positive_row_targets, positive_col_targets)
        / positive_total
    )

    proportional_seed[~allowed] = 0.0

    positive_seed = (
        (1.0 - blend) * positive_seed
        + blend * proportional_seed
    )

    # ---------------------------------------------------------
    # 4. Check whether positive targets have seed support.
    # ---------------------------------------------------------
    unsupported_rows = [
        i for i in range(len(positive_row_targets))
        if positive_row_targets[i] > tol
        and positive_seed[i, :].sum() <= tol
    ]

    unsupported_cols = [
        j for j in range(len(positive_col_targets))
        if positive_col_targets[j] > tol
        and positive_seed[:, j].sum() <= tol
    ]

    if unsupported_rows or unsupported_cols:
        raise ValueError(
            f"{country}: {mode} RAS has positive residual targets "
            "with no positive seed support. "
            f"Unsupported rows: {unsupported_rows}; "
            f"unsupported columns: {unsupported_cols}."
        )

    # ---------------------------------------------------------
    # 5. Iterative proportional fitting.
    # ---------------------------------------------------------
    history = {
        "Row margins": [],
        "Column margins": [],
    }

    for it in range(max_iter):

        # Row adjustment.        row_sums = positive_seed.sum(axis=1)

        row_factors = np.divide(
            positive_row_targets,
            row_sums,
            out=np.ones_like(positive_row_targets),
            where=row_sums > 0,
        )

        positive_seed *= row_factors[:, None]

        # Column adjustment.
        col_sums = positive_seed.sum(axis=0)

        col_factors = np.divide(
            positive_col_targets,
            col_sums,
            out=np.ones_like(positive_col_targets),
            where=col_sums > 0,
        )

        positive_seed *= col_factors[None, :]

        # Convergence checks.
        row_error = np.max(
            np.abs(positive_seed.sum(axis=1) - positive_row_targets)
        )

        col_error = np.max(
            np.abs(positive_seed.sum(axis=0) - positive_col_targets)
        )

        history["Row margins"].append(row_error)
        history["Column margins"].append(col_error)

        error = max(row_error, col_error)

        if error < tol:
            break

    error = max(
        history["Row margins"][-1],
        history["Column margins"][-1],
    )

    if error > 1e-6:
        print(
            f"{country}: WARNING {mode} RAS did not converge "
            f"(final max error {error:.2e})."
        )

    # ---------------------------------------------------------
    # 6. Restore the original negative transactions.
    # ---------------------------------------------------------
    balanced = positive_seed + negative

    # Verify the final signed margins.
    final_row_error = np.max(
        np.abs(balanced.sum(axis=1) - row_targets)
    )

    final_col_error = np.max(
        np.abs(balanced.sum(axis=0) - col_targets)
    )

    final_error = max(final_row_error, final_col_error)

    if final_error > 1e-6:
        print(
            f"{country}: WARNING {mode} signed margins differ "
            f"from targets (final max error {final_error:.2e})."
        )

    return balanced, history, it + 1, final_error


def compare_ras_cells(
    before,
    after,
    label,
    out_dir,
    atol=1e-10,
    rtol=1e-9,
):
    """
    Compare every cell in the original IO matrix with its RAS-balanced
    counterpart and export the results to Excel.

    Parameters
    ----------
    before : pd.DataFrame
        Original IO matrix before RAS.
    after : pd.DataFrame
        IO matrix after RAS.
    label : str
        Identifier used in the output filename.
    out_dir : str or Path
        Directory in which to save the Excel comparison.
    atol : float
        Absolute tolerance for identifying changed cells.
    rtol : float
        Relative tolerance for identifying changed cells.

    Returns
    -------
    dict
        DataFrames containing all cell comparisons, changed cells,
        and row/column summaries.
    """

    # Check that both matrices have the same row and column labels.
    missing_rows = before.index.difference(after.index)
    extra_rows = after.index.difference(before.index)
    missing_cols = before.columns.difference(after.columns)
    extra_cols = after.columns.difference(before.columns)

    if (
        len(missing_rows)
        or len(extra_rows)
        or len(missing_cols)
        or len(extra_cols)
    ):
        raise ValueError(
            "The matrices do not have matching row and column labels.\n"
            f"Missing rows: {list(missing_rows)}\n"
            f"Extra rows: {list(extra_rows)}\n"
            f"Missing columns: {list(missing_cols)}\n"
            f"Extra columns: {list(extra_cols)}"
        )

    # Align the matrices to ensure that corresponding cells are compared.
    after = after.reindex(index=before.index, columns=before.columns)

    before_values = before.to_numpy(dtype=float)
    after_values = after.to_numpy(dtype=float)

    # Calculate cell-level changes.
    changes = after_values - before_values

    pct_changes = np.full(before_values.shape, np.nan, dtype=float)

    np.divide(
        changes,
        before_values,
        out=pct_changes,
        where=np.abs(before_values) > atol,
    )
    pct_changes *= 100.0

    # Identify cells that changed beyond the specified numerical tolerance.
    changed = ~np.isclose(
        before_values,
        after_values,
        atol=atol,
        rtol=rtol,
        equal_nan=True,
    )

    # Construct a comparison table containing every cell.
    rows = []
    for i, row in enumerate(before.index):
        for j, col in enumerate(before.columns):
            rows.append(
                {
                    "row": row,
                    "column": col,
                    "before": before_values[i, j],
                    "after": after_values[i, j],
                    "absolute_change": changes[i, j],
                    "percentage_change": pct_changes[i, j],
                    "changed": changed[i, j],
                }
            )

    all_cells = pd.DataFrame(rows)

    # Keep only cells that changed beyond numerical tolerance.
    changed_cells = (
        all_cells.loc[all_cells["changed"]]
        .sort_values(
            "absolute_change",
            key=lambda x: x.abs(),
            ascending=False,
        )
        .reset_index(drop=True)
    )

    # Summarize changes by row.
    row_summary = (
        all_cells.groupby("row", sort=False)
        .agg(
            number_of_cells=("column", "count"),
            number_changed=("changed", "sum"),
            net_change=("absolute_change", "sum"),
            total_absolute_change=(
                "absolute_change",
                lambda x: x.abs().sum(),
            ),
        )
        .reset_index()
    )

    # Summarize changes by column.
    column_summary = (
        all_cells.groupby("column", sort=False)
        .agg(
            number_of_cells=("row", "count"),
            number_changed=("changed", "sum"),
            net_change=("absolute_change", "sum"),
            total_absolute_change=(
                "absolute_change",
                lambda x: x.abs().sum(),
            ),
        )
        .reset_index()
    )

    # Export the results.
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    output_path = out_dir / f"{label}_RAS_cell_comparison.xlsx"

    with pd.ExcelWriter(output_path) as writer:
        all_cells.to_excel(
            writer,
            sheet_name="All cells",
            index=False,
        )
        changed_cells.to_excel(
            writer,
            sheet_name="Changed cells",
            index=False,
        )
        row_summary.to_excel(
            writer,
            sheet_name="Row summary",
            index=False,
        )
        column_summary.to_excel(
            writer,
            sheet_name="Column summary",
            index=False,
        )

    print(f"\nRAS cell comparison saved to: {output_path}")
    print(f"Total cells compared: {len(all_cells):,}")
    print(f"Cells changed: {len(changed_cells):,}")
    print(
        f"Maximum absolute cell change: "        f"{all_cells['absolute_change'].abs().max():.10g}"
    )

    return {
        "all_cells": all_cells,
        "changed_cells": changed_cells,
        "row_summary": row_summary,
        "column_summary": column_summary,
    }


def diagnose_ras(before, after, country, out_dir=None):
    """
    Compare sales-side and cost-side technology allocations before
    and after RAS. Optionally save the diagnostics to Excel.
    """
    techs = [
        c for c in after.columns
        if c in ACTIVITIES and f"DOM_{c}" in after.index
    ]
    new_rows = [f"DOM_{t}" for t in techs]

    cols = [
        c for c in after.columns
        if c not in techs and c != "TOTAL"
    ]
    ind_cols = [c for c in cols if f"DOM_{c}" in after.index]
    fd_cols = [c for c in cols if c not in ind_cols]

    rows = [r for r in after.index if r not in new_rows]
    dom = [r for r in rows if r.startswith("DOM_")]
    imp = [
        r for r in rows
        if r.startswith("IMP_") and r != "IMP_OTHER"
    ]
    tax = [
        r for r in ("IMP_OTHER", "TXS_INT_FNL")
        if r in rows
    ]

    def split(t):
        sales = pd.DataFrame({
            "sales_to_industry": t.loc[new_rows, ind_cols].sum(axis=1).to_numpy(),
            "sales_to_final_demand": t.loc[new_rows, fd_cols].sum(axis=1).to_numpy(),
            "sales_to_electricity": t.loc[new_rows, techs].sum(axis=1).to_numpy(),
        }, index=techs)

        costs = pd.DataFrame({
            "dom_inputs": t.loc[dom, techs].sum(axis=0),
            "imp_inputs": t.loc[imp, techs].sum(axis=0),
            "taxes_other": t.loc[tax, techs].sum(axis=0),
            "value_added": t.loc["VALU", techs],
            "electricity_inputs": t.loc[new_rows, techs].sum(axis=0),
        }, index=techs)

        return sales, costs

    aligned_before = before.reindex(
        index=after.index,
        columns=after.columns,
    )

    sales_before, costs_before = split(aligned_before)
    sales_after, costs_after = split(after)

    sales_change = sales_after - sales_before
    costs_change = costs_after - costs_before

    print(f"\n{country}: ROW side (sales), before | after | change")
    print(
        pd.concat({
            "before": sales_before,
            "after": sales_after,
            "change": sales_change,
        }, axis=1).round(3).to_string()
    )

    print(f"\n{country}: COLUMN side (costs), before | after | change")
    print(
        pd.concat({
            "before": costs_before,
            "after": costs_after,
            "change": costs_change,
        }, axis=1).round(3).to_string()
    )

    if out_dir is not None:
        path = Path(out_dir) / f"{country}_RAS_diagnostics.xlsx"

        with pd.ExcelWriter(path) as writer:
            sales_before.to_excel(writer, sheet_name="Sales before")
            sales_after.to_excel(writer, sheet_name="Sales after")
            sales_change.to_excel(writer, sheet_name="Sales change")

            costs_before.to_excel(writer, sheet_name="Costs before")
            costs_after.to_excel(writer, sheet_name="Costs after")
            costs_change.to_excel(writer, sheet_name="Costs change")

        print(f"{country}: RAS diagnostics saved to {path}")

    return {
        "sales_before": sales_before,
        "sales_after": sales_after,
        "sales_change": sales_change,
        "costs_before": costs_before,
        "costs_after": costs_after,
        "costs_change": costs_change,
    }


def RAS(matrix, country, mode="intermediate", blend=0.0,
        out_dir=None, max_iter=5000, tol=1e-9):
    """
    Balance either:

    mode="intermediate":
        All intermediate supplier rows, including DOM_*, IMP_* and
        intermediate taxes, against all industry purchaser columns.
        VALU and final demand remain outside the RAS.

    mode="full":
        All supplier rows, including VALU, against all industry and
        final-demand columns.
    """
    techs = [
        c for c in matrix.columns
        if c in ACTIVITIES and f"DOM_{c}" in matrix.index
    ]
    new_rows = [f"DOM_{t}" for t in techs]

    # Industry columns are those with a corresponding domestic supplier
    # row, plus the newly disaggregated electricity columns.
    industry_cols = [
        c for c in matrix.columns
        if c != "TOTAL" and (f"DOM_{c}" in matrix.index or c in techs)
    ]
    final_demand_cols = [
        c for c in matrix.columns
        if c not in industry_cols and c != "TOTAL"
    ]

    if mode == "intermediate":
        # All intermediate suppliers, including disaggregated imports
        # and taxes. Exclude value added and accounting rows.
        balance_rows = [
            r for r in matrix.index
            if r not in ("TTL_INT_FNL", "OUTPUT", "VALU")
        ]
        balance_cols = industry_cols

        row_targets = matrix.loc[
            balance_rows, balance_cols
        ].sum(axis=1).to_numpy(float)

        col_targets = matrix.loc[
            "TTL_INT_FNL", balance_cols
        ].to_numpy(float)

    elif mode == "full":
        # Include value added and final demand in the balanced matrix.
        balance_rows = [
            r for r in matrix.index
            if r not in ("TTL_INT_FNL", "OUTPUT")
        ]
        balance_cols = industry_cols + final_demand_cols

        row_targets = matrix.loc[
            balance_rows, "TOTAL"
        ].to_numpy(float)

        col_targets = np.array([
            matrix.loc["OUTPUT", c] if c in industry_cols
            else matrix[c].drop(labels=["TTL_INT_FNL", "OUTPUT"],
                                errors="ignore").sum()
            for c in balance_cols
        ], dtype=float)

    else:
        raise ValueError(
            f"Unknown RAS mode {mode!r}. "
            "Choose 'intermediate' or 'full'."
        )

    seed = matrix.loc[balance_rows, balance_cols].to_numpy(float)
    
        # Diagnose negative transactions before balancing.
    negative_positions = np.argwhere(seed < -1e-12)

    if len(negative_positions):
        negative_cells = pd.DataFrame(
            [
                {
                    "row": balance_rows[i],
                    "column": balance_cols[j],
                    "value": seed[i, j],
                }
                for i, j in negative_positions
            ]
        )

        print(
            f"{country}: found {len(negative_cells)} negative "
            f"transactions in the {mode} RAS seed."
        )
        print(negative_cells.to_string(index=False))
        print(
            f"Total negative transactions: "
            f"{negative_cells['value'].sum():.6f}"
        )

    balanced, history, iterations, error = _ras_balance(
        seed=seed,
        row_targets=row_targets,
        col_targets=col_targets,
        country=country,
        mode=mode,
        blend=blend,
        max_iter=max_iter,
        tol=tol,
    )

    out = matrix.copy()
    out.loc[balance_rows, balance_cols] = balanced

    # Recalculate accounting rows for all industry columns.
    parts = [
        r for r in out.index
        if r not in ("TTL_INT_FNL", "OUTPUT", "VALU")
    ]

    out.loc["TTL_INT_FNL", industry_cols] = (
        out.loc[parts, industry_cols].sum(axis=0).to_numpy()
    )
    out.loc["OUTPUT", industry_cols] = (
        out.loc["TTL_INT_FNL", industry_cols]
        + out.loc["VALU", industry_cols]
    ).to_numpy()

    if mode == "full":
        out.loc[balance_rows, "TOTAL"] = (
            out.loc[balance_rows, balance_cols].sum(axis=1).to_numpy()
        )

    # Recalculate the new domestic electricity supplier row totals.
    out.loc[new_rows, "TOTAL"] = (
        out.loc[new_rows]
        .drop(columns="TOTAL")
        .sum(axis=1)
        .to_numpy()
    )
    # Check the margins actually imposed by RAS.
    row_check = out.loc[balance_rows, balance_cols].sum(axis=1).to_numpy()
    col_check = out.loc[balance_rows, balance_cols].sum(axis=0).to_numpy()

    if not np.allclose(row_check, row_targets, rtol=1e-6, atol=1e-6):
        raise AssertionError(f"{country}: {mode} RAS row margins not met.")

    if not np.allclose(
        col_check,
        col_targets * row_targets.sum() / col_targets.sum(),
        rtol=1e-6,
        atol=1e-6,
    ):
        raise AssertionError(f"{country}: {mode} RAS column margins not met.")

    print(
        f"{country}: {mode} RAS completed in {iterations} iterations; "
        f"final maximum error = {error:.2e}."
    )

    # Save a separate convergence plot for each RAS variant.
    fig, ax = plt.subplots(figsize=(8, 5))
    for label, values in history.items():
        ax.semilogy(
            np.arange(1, len(values) + 1),
            np.maximum(values, 1e-16),
            label=label,
        )

    ax.axhline(
        tol, color="grey", linestyle="--", linewidth=1,
        label=f"tolerance ({tol:g})",
    )
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Maximum absolute margin error")
    ax.set_title(
        f"{country}: {mode} RAS "
        f"({iterations} iterations, final error {error:.1e})"
    )
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()

    if out_dir is not None:
        path = Path(out_dir) / f"{country}_{mode}_RAS_convergence.png"
        fig.savefig(path, dpi=150)
        print(f"{country}: convergence graph saved to {path}")

    plt.close(fig)

    return out


def compare_ras(before, after, country, out_dir=None, top_n=15, tol=1e-3):
    """
    Compare the disaggregated table before and after RAS.

    Prints: (1) block-level summary, (2) per-technology totals,
    (3) the top_n largest cell changes. Optionally writes an Excel
    workbook with Before / After / Difference / Pct change sheets.
    """
    techs = [c for c in after.columns if c in ACTIVITIES and f"DOM_{c}" in after.index]
    new_rows = [f"DOM_{t}" for t in techs]
    total_rows = ["TTL_INT_FNL", "OUTPUT"]
    base_rows = [r for r in after.index if r not in new_rows and r not in total_rows]
    base_cols = [c for c in after.columns if c not in techs and c != "TOTAL"]

    before = before.reindex(index=after.index, columns=after.columns)
    diff = after - before
    pct = diff / before.where(before.abs() > tol) * 100   # NaN where the base is ~0

    # ---- 1. Block-level summary ------------------------------------------
    blocks = {
        "Supply row -> original columns": (new_rows, base_cols),
        "Electricity -> electricity":     (new_rows, techs),
        "Inputs -> new columns":          (base_rows, techs),
        "Untouched (should be ~0)":       (base_rows, base_cols),
    }
    rows = []
    for name, (r, c) in blocks.items():
        b, a, d = before.loc[r, c].to_numpy(), after.loc[r, c].to_numpy(), diff.loc[r, c].to_numpy()
        rows.append({
            "block": name,
            "cells": d.size,
            "sum_before": b.sum(),
            "sum_after": a.sum(),
            "total_abs_change": np.abs(d).sum(),
            "max_abs_change": np.abs(d).max(),
            "cells_changed": int((np.abs(d) > tol).sum()),
        })
    block_summary = pd.DataFrame(rows).set_index("block")

    print(f"\n{'=' * 70}\n{country}: before vs after RAS\n{'=' * 70}")
    print(block_summary.round(3).to_string())

    # ---- 2. Per-technology totals ----------------------------------------
    tech_summary = pd.DataFrame({
        "row_before": before.loc[new_rows, "TOTAL"].to_numpy(),
        "row_after": after.loc[new_rows, "TOTAL"].to_numpy(),
        "col_before": before.loc["OUTPUT", techs].to_numpy(),
        "col_after": after.loc["OUTPUT", techs].to_numpy(),
    }, index=techs)
    tech_summary["row_change_%"] = (
        (tech_summary.row_after / tech_summary.row_before.where(tech_summary.row_before > tol) - 1) * 100
    )
    tech_summary["col_change_%"] = (
        (tech_summary.col_after / tech_summary.col_before.where(tech_summary.col_before > tol) - 1) * 100
    )
    print("\nPer-technology totals:")
    print(tech_summary.round(3).to_string())

    # ---- 3. Largest cell changes ------------------------------------------
    region = pd.DataFrame(False, index=after.index, columns=after.columns)
    region.loc[new_rows, :] = True
    region.loc[:, techs] = True
    region.loc[total_rows, :] = False
    region["TOTAL"] = False

    s = diff.where(region).stack().dropna()
    top_idx = s.abs().sort_values(ascending=False).head(top_n).index
    top = pd.DataFrame(
        {
            "before": [before.at[r, c] for r, c in top_idx],
            "after": [after.at[r, c] for r, c in top_idx],
            "change": [diff.at[r, c] for r, c in top_idx],
            "change_%": [pct.at[r, c] for r, c in top_idx],
        },
        index=pd.MultiIndex.from_tuples(top_idx, names=["row", "column"]),
    )
    print(f"\nTop {top_n} cell changes:")
    print(top.round(3).to_string())
    
    
    # ---- 3b. Cell-by-cell comparison -------------------------------------
    cell_comparison = pd.DataFrame({
        "row": np.repeat(after.index.to_numpy(), len(after.columns)),
        "column": np.tile(after.columns.to_numpy(), len(after.index)),
        "before": before.to_numpy().ravel(),
        "after": after.to_numpy().ravel(),
        "absolute_change": diff.to_numpy().ravel(),
        "percentage_change": pct.to_numpy().ravel(),
    })

    cell_comparison["changed"] = (
        cell_comparison["absolute_change"].abs() > tol
    )

    changed_cells = (
        cell_comparison.loc[cell_comparison["changed"]]
        .sort_values(
            "absolute_change",
            key=lambda x: x.abs(),
            ascending=False,
        )
        .reset_index(drop=True)
    )

    print(f"\nCell-by-cell comparison:")
    print(f"Total cells: {len(cell_comparison):,}")
    print(f"Changed cells (absolute change > {tol:g}): "
          f"{len(changed_cells):,}")

    # ---- 4. Optional Excel export -----------------------------------------
    if out_dir is not None:
        path = Path(out_dir) / f"{country}_RAS_comparison.xlsx"
        with pd.ExcelWriter(path) as w:
            block_summary.to_excel(w, sheet_name="Block summary")
            tech_summary.to_excel(w, sheet_name="Technology totals")
            top.to_excel(w, sheet_name="Top changes")
            cell_comparison.to_excel(w, sheet_name="All cells", index=False)
            changed_cells.to_excel(w, sheet_name="Changed cells", index=False)
            before.to_excel(w, sheet_name="Before")
            after.to_excel(w, sheet_name="After")
            diff.to_excel(w, sheet_name="Difference")
            pct.to_excel(w, sheet_name="Pct change")

        print(f"\nSaved: {path}")

    return diff, pct

FD_COLS_EXCLUDE = None  # final demand = columns with no matching DOM_ row

def main(args=None):
    parsed_args = parse_args(args)

    root = Path(parsed_args.io_database_root)
    output_dir = root / "Disaggregated IO"
    output_dir.mkdir(parents=True, exist_ok=True)

    countries = parsed_args.countries_to_share.split()

    # ---------------------------------------------------------
    # 1. Read the OECD domestic IO tables.
    # ---------------------------------------------------------
    data = data_in(parsed_args)

    # ---------------------------------------------------------
    # 2. Calculate domestic and import allocation shares.
    # ---------------------------------------------------------
    (
        result_col,
        result_row,
        result_oecd_col,
        result_oecd_row,
        oecd_levels,
        raw_levels,
        result_import_row,
        result_import_oecd_row,
    ) = calculate_shares([
        "--country_columns", "LVA LTU EST",
        "--io_database_root", parsed_args.io_database_root,
        "--year", parsed_args.year,
    ])

    # ---------------------------------------------------------
    # 3. Disaggregate domestic electricity and construct
    #    the available import-allocation alternatives.
    # ---------------------------------------------------------
    alternatives_data = {}

    for country in countries:
        country_code = COUNTRY_CODES[country]

        print(f"\n{'=' * 70}")
        print(f"{country}: domestic electricity disaggregation")
        print(f"{'=' * 70}")

        # Load the country-specific JRC-IDEES generation data and GTAP
        # emissions accounts. The JRC generation mix is used for the 2023
        # output allocation; GTAP emissions distinguish base/peak activities.
        powergen_workbook = find_country_workbook(
            root, country, parsed_args.jrc_powergen_pattern
        )
        aea_workbook = find_country_workbook(
            root, country, parsed_args.aea_emissions_pattern.format(country=country)
        )
        co2_gtap, nonco2_gtap = load_gtap_emissions_weights(
            Path(parsed_args.co2_gdx),
            Path(parsed_args.nonco2_gdx),
            country_code,
            system_directory=parsed_args.gams_system_directory,
        )
        jrc_output_shares = build_jrc_output_shares(
            powergen_workbook,
            country_code,
            raw_levels,
            year=2023,
        )
        output_shares_path = output_dir / f"{country_code}2023_JRC_generation_shares.csv"
        jrc_output_shares.rename("output_share").to_csv(output_shares_path, header=True)
        print(f"{country}: saved JRC generation output shares: {output_shares_path}")

        emissions_allocation = allocate_oecd_aea_emissions(
            powergen_workbook,
            aea_workbook,
            co2_gtap,
            nonco2_gtap,
            year=2023,
            country_code=country_code,
        )
        emissions_path = output_dir / f"{country_code}2023_AEA_D35_GTAP_emissions.csv"
        emissions_allocation.to_csv(emissions_path, index=False)
        print(f"{country}: saved AEA electricity/heat emissions allocation: {emissions_path}")

        # First disaggregate domestic electricity.
        # IMP_D remains available at this stage.
        io_domestic = disaggregate_electricity(
            data[country]["DOM_D"],
            country_code,
            result_oecd_row,
            oecd_levels,
            raw_levels,
            drop_inactive=parsed_args.drop_inactive,
            jrc_output_shares=jrc_output_shares,
        )

        # Convert the OECD-level VMFB results into the format
        # expected by create_import_alternatives().
        vmfb_import_shares = format_vmfb_import_shares(
            result_import_oecd_row,
            country_code,
        )

        print(f"\n{'=' * 70}")
        print(f"{country}: import disaggregation")
        print(f"{'=' * 70}")

        io_eia, io_gtap = add_import_alternatives(
            io_domestic_disaggregated=io_domestic,
            country=country,
            country_code=country_code,
            year=parsed_args.year,
            root=root,
            output_folder=output_dir,
            vmfb_import_shares=vmfb_import_shares,
        )

        # Only add alternatives that were actually generated.
        alternatives_data[country] = {
            "GTAP": io_gtap,
        }

        if io_eia is not None:
            alternatives_data[country]["EIA"] = io_eia

    # ---------------------------------------------------------
    # 4. Run both RAS modes and all sensitivity specifications.
    # ---------------------------------------------------------
    rebalanced_data = {}

    for country, alternatives in alternatives_data.items():
        rebalanced_data[country] = {}

        for alternative, matrix in alternatives.items():

            for mode in ("intermediate", "full"):

                for sensitivity, blend in RAS_SENSITIVITIES.items():

                    label = (
                        f"{country} - {alternative} - "
                        f"{mode} - {sensitivity}"
                    )

                    print(f"\n{'=' * 70}")
                    print(f"{label}: RAS")
                    print(f"{'=' * 70}")

                    balanced = RAS(
                        matrix,
                        label,
                        mode=mode,
                        blend=blend,
                        out_dir=output_dir,
                    )
                    
                    cell_comparison = compare_ras_cells(                        matrix,
                        balanced,
                        label,
                        output_dir,
                    )

                    compare_ras(
                        matrix,
                        balanced,
                        label,
                        out_dir=output_dir,
                    )

                    diagnostics = diagnose_ras(
                        matrix,
                        balanced,
                        label,
                        out_dir=output_dir,
                    )

                    key = f"{alternative}_{mode}_{sensitivity}"

                    rebalanced_data[country][key] = balanced

    # ---------------------------------------------------------
    # 5. Export the final rebalanced alternatives.
    # ---------------------------------------------------------
    for country, alternatives in rebalanced_data.items():
        country_code = COUNTRY_CODES[country]

        for alternative, matrix in alternatives.items():
            output_path = (
                output_dir
                / f"{country_code}{parsed_args.year}_"
                  f"{alternative.lower()}_disaggregated.csv"
            )

            matrix.to_csv(output_path)
            print(f"Saved: {output_path}")

    return rebalanced_data

if __name__ == "__main__":
    main(sys.argv[1:])