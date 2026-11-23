"""
Project a disaggregated, already-RAS-balanced OECD IO table to 2030 (BAU) and
rebalance it with RAS; project the environmental extension alongside it.

Method (follows the paper's BAU scenario)
-----------------------------------------
* Every sector has a cumulative growth factor g (Table C1, see growth.py).
  All 12 generating technologies take the electricity factor; GDT takes the
  gas, steam and air-conditioning factor.
* Industry column j (every cost entry: DOM_*, IMP_*, taxes, VALU) is scaled by
  g_j. Because all inputs scale with output, 2022 technical, import and
  value-added coefficients are preserved ("inputs all grow proportionally to
  output"), exactly the paper's fixed-coefficient assumption.
* Final-demand cell (supplier i, final-demand column f) is scaled by g_i, the
  growth of the *supplying* sector (the paper projects final demand with the
  sectoral factors). Cells on non-sector rows (VALU, taxes, IMP_OTHER) in a
  final-demand column are scaled by that column's |value|-weighted growth.
* The projected seed no longer balances: row i is scaled by buyers' growth but
  sector i grows at g_i. RAS (the repository's existing `_ras_balance`) then
  rebalances to row targets g_i x (2022 row total) for the domestic (DOM_*)
  rows and column targets g_j x (2022 column total), so projected sector
  output is exactly g x 2022 output. Import and tax rows follow buyers'
  demand and are the free margin that closes the grand total ("intermediate"
  mode); in "full" mode that margin is the final-demand columns.
* Emissions: constant emission intensity per unit of output (paper Eq. 3-4:
  g = e_hat (I-A)^-1 y), i.e. emissions grow with sector output.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from growth import growth_factor

ACCOUNT_ROWS = ("TTL_INT_FNL", "OUTPUT")
NON_SECTOR_ROWS = ("VALU", "IMP_OTHER", "TXS_INT_FNL")


def _activities():
    from shares_gams import ACTIVITIES
    return ACTIVITIES


def table_layout(matrix: pd.DataFrame):
    """Same industry / final-demand split the RAS routine uses."""
    activities = _activities()
    techs = [c for c in matrix.columns
             if c in activities and f"DOM_{c}" in matrix.index]
    industry_cols = [c for c in matrix.columns
                     if c != "TOTAL" and (f"DOM_{c}" in matrix.index or c in techs)]
    fd_cols = [c for c in matrix.columns
               if c not in industry_cols and c != "TOTAL"]
    return techs, industry_cols, fd_cols


def growth_vectors(matrix: pd.DataFrame, country_code: str):
    """
    Returns (g_col, g_row, fd_cols).
      g_col: growth of each industry column
      g_row: growth of each DOM_*/IMP_* supplier row (NaN for VALU, taxes, IMP_OTHER)
    """
    activities = _activities()
    _, industry_cols, fd_cols = table_layout(matrix)

    unmapped = []

    def lookup(label):
        try:
            return growth_factor(label, country_code, activities)
        except KeyError:
            unmapped.append(label)
            return np.nan

    g_col = pd.Series({c: lookup(c) for c in industry_cols})
    g_row = pd.Series(np.nan, index=matrix.index, dtype=float)
    for r in matrix.index:
        if r in ACCOUNT_ROWS or r in NON_SECTOR_ROWS:
            continue
        if r.startswith(("DOM_", "IMP_")):
            g_row[r] = lookup(r.split("_", 1)[1])
    if unmapped:
        raise KeyError(
            "No Table C1 growth factor for these sector labels: "
            f"{sorted(set(unmapped))}. Add them to growth.TABLE_C1 / growth.ALIASES."
        )
    return g_col, g_row, fd_cols


def project_table(matrix: pd.DataFrame, country_code: str):
    """Scale the table to 2030 without rebalancing. Returns (seed_frame, g_col, g_row)."""
    g_col, g_row, fd_cols = growth_vectors(matrix, country_code)
    _, industry_cols, _ = table_layout(matrix)
    body = [r for r in matrix.index if r not in ACCOUNT_ROWS]

    seed = matrix.copy().astype(float)

    # Industry columns: every cost entry scales with the column's own output.
    seed.loc[body, industry_cols] = (
        matrix.loc[body, industry_cols].to_numpy(float) * g_col.to_numpy()[None, :]
    )

    # Final-demand columns: scale by the supplier row's growth.
    sector_rows = [r for r in body if not np.isnan(g_row[r])]
    free_rows = [r for r in body if np.isnan(g_row[r])]
    for f in fd_cols:
        vals = matrix.loc[sector_rows, f].to_numpy(float)
        seed.loc[sector_rows, f] = vals * g_row[sector_rows].to_numpy()
        w = np.abs(vals)
        gf = (w * g_row[sector_rows].to_numpy()).sum() / w.sum() if w.sum() > 0 else 1.0
        seed.loc[free_rows, f] = matrix.loc[free_rows, f].to_numpy(float) * gf
    return seed, g_col, g_row


def _closing_message(name, s):
    flag = "" if 0.5 < s < 2.0 else "  <-- large adjustment, check the growth inputs"
    print(f"    {name} closing factor = {s:.5f}{flag}")


def project_and_rebalance(matrix, country, country_code, ras_balance, mode="full",
                          blend=0.0, max_iter=5000, tol=1e-9):
    """
    Project an already-balanced disaggregated table to 2030 and rebalance with RAS.

    ras_balance : the repository's `_ras_balance` (application.py), passed in so
                  this module always uses whichever version the project runs.
                  Called as ras_balance(seed=, row_targets=, col_targets=,
                  country=, mode=, blend=, max_iter=, tol=) and must return
                  (balanced, history, iterations, error).
    `mode` and `blend` should match those used for the 2022 balancing.
    """
    if mode not in ("intermediate", "full"):
        raise ValueError(f"Unknown RAS mode {mode!r}. Choose 'intermediate' or 'full'.")

    techs, industry_cols, fd_cols = table_layout(matrix)
    seed_frame, g_col, g_row = project_table(matrix, country_code)

    if mode == "intermediate":
        balance_rows = [r for r in matrix.index
                        if r not in ACCOUNT_ROWS and r != "VALU"]
        balance_cols = industry_cols
    else:
        balance_rows = [r for r in matrix.index if r not in ACCOUNT_ROWS]
        balance_cols = industry_cols + fd_cols

    # Only domestic supplier rows are pinned to their sector's growth. Imports,
    # taxes and VALU follow buyers' demand (2022 coefficients held), so they are
    # free margins that absorb the closing difference.
    sector_row = np.array([r.startswith("DOM_") for r in balance_rows])
    g_r = np.array([g_row[r] if s else np.nan for r, s in zip(balance_rows, sector_row)])

    # 2022 margins of the balanced table over the balanced block.
    row0 = matrix.loc[balance_rows, balance_cols].sum(axis=1).to_numpy(float)
    col0_ind = matrix.loc[balance_rows, industry_cols].sum(axis=0).to_numpy(float)

    seed = seed_frame.loc[balance_rows, balance_cols].to_numpy(float)

    row_targets = np.where(sector_row, g_r * row0, seed.sum(axis=1))
    col_targets = seed.sum(axis=0).astype(float)
    col_targets[:len(industry_cols)] = g_col.to_numpy() * col0_ind

    # Close the grand total using the free margin.
    if mode == "intermediate":
        free = ~sector_row
        need = col_targets.sum() - row_targets[sector_row].sum()
        s = need / row_targets[free].sum() if row_targets[free].sum() != 0 else np.nan
        if not np.isfinite(s) or s <= 0:
            raise ValueError(f"{country}: cannot close grand total with import / tax rows.")
        row_targets[free] *= s
        _closing_message("import / tax rows", s)
    else:
        n = len(industry_cols)
        need = row_targets.sum() - col_targets[:n].sum()
        fd_seed = col_targets[n:].sum()
        s = need / fd_seed if fd_seed != 0 else np.nan
        if not np.isfinite(s) or s <= 0:
            raise ValueError(f"{country}: cannot close grand total with final-demand columns.")
        col_targets[n:] *= s
        _closing_message("final-demand columns", s)

    balanced, history, iterations, error = ras_balance(
        seed=seed, row_targets=row_targets, col_targets=col_targets,
        country=country, mode=mode, blend=blend, max_iter=max_iter, tol=tol,
    )

    out = seed_frame.copy()
    out.loc[balance_rows, balance_cols] = balanced

    # Accounting rows for industry columns.
    parts = [r for r in out.index if r not in ACCOUNT_ROWS and r != "VALU"]
    out.loc["TTL_INT_FNL", industry_cols] = out.loc[parts, industry_cols].sum(axis=0).to_numpy()
    out.loc["OUTPUT", industry_cols] = (
        out.loc["TTL_INT_FNL", industry_cols] + out.loc["VALU", industry_cols]
    ).to_numpy()

    # Accounting cells in final-demand columns: scale with the column's realised growth.
    body = [r for r in out.index if r not in ACCOUNT_ROWS]
    for f in fd_cols:
        old = matrix.loc[body, f].sum()
        ratio = out.loc[body, f].sum() / old if old != 0 else 1.0
        for acc in ACCOUNT_ROWS:
            out.loc[acc, f] = matrix.loc[acc, f] * ratio

    # Row totals; the two accounting rows keep their 2022 ratio to the industry total.
    out.loc[body, "TOTAL"] = out.loc[body, balance_cols if mode == "full"
                                     else industry_cols + fd_cols].sum(axis=1).to_numpy()
    for acc in ACCOUNT_ROWS:
        old_ind = matrix.loc[acc, industry_cols].sum()
        ratio = out.loc[acc, industry_cols].sum() / old_ind if old_ind != 0 else 1.0
        out.loc[acc, "TOTAL"] = matrix.loc[acc, "TOTAL"] * ratio

    # ---- Checks -------------------------------------------------------------
    realised = out.loc["OUTPUT", industry_cols] / matrix.loc["OUTPUT", industry_cols].replace(0, np.nan)
    diff = (realised - g_col).abs().dropna()
    if len(diff) and diff.max() > 1e-6:
        raise AssertionError(
            f"{country}: projected output does not match growth factors "
            f"(max deviation {diff.max():.2e} in {diff.idxmax()})."
        )
    if not np.allclose(out.loc[balance_rows, balance_cols].sum(axis=1), row_targets,
                       rtol=1e-6, atol=1e-6):
        raise AssertionError(f"{country}: projected {mode} RAS row margins not met.")
    print(f"{country}: projected {mode} RAS completed in {iterations} iterations; "
          f"final maximum error = {error:.2e}; "
          f"total output {matrix.loc['OUTPUT', industry_cols].sum():.1f} -> "
          f"{out.loc['OUTPUT', industry_cols].sum():.1f}.")

    return out, {"g_col": g_col, "g_row": g_row, "iterations": iterations, "error": error}


# ----------------------------------------------------------------------------
# Environmental extension
# ----------------------------------------------------------------------------
def project_emissions(extension: pd.DataFrame, before: pd.DataFrame, after: pd.DataFrame):
    """
    Grow the environmental extension with the projected output.

    Paper Eq. 3-4: emissions are e_hat x with a constant emission intensity
    e_hat = E / x, so each sector's emissions grow with its output. Final-demand
    (e.g. household direct) emissions grow with the growth of that
    final-demand column's total.

    extension : rows = stressors (e.g. CO2e, CH4), columns = labels of the
                disaggregated table's industry (and optionally final-demand)
                columns, in the same units for every column.
    """
    _, industry_cols, fd_cols = table_layout(before)
    missing = [c for c in extension.columns if c not in industry_cols + fd_cols]
    if missing:
        raise KeyError(f"Extension columns not in the IO table: {missing}")

    body = [r for r in before.index if r not in ACCOUNT_ROWS]
    growth = {}
    for c in extension.columns:
        if c in industry_cols:
            x0, x1 = before.loc["OUTPUT", c], after.loc["OUTPUT", c]
        else:
            x0, x1 = before.loc[body, c].sum(), after.loc[body, c].sum()
        growth[c] = x1 / x0 if x0 != 0 else 1.0
    growth = pd.Series(growth)

    projected = extension.astype(float) * growth[extension.columns].to_numpy()[None, :]
    return projected, growth


def allocate_electricity_emissions(total_electricity_emissions, output, relative_intensity):
    """Paper Eq. 3: E_k = E_elec * x_k r_k / sum_k(x_k r_k)  (all arguments by technology k)."""
    w = pd.Series(output, dtype=float) * pd.Series(relative_intensity, dtype=float)
    return total_electricity_emissions * w / w.sum()
