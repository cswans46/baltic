"""
Leontief (cost-push) IO price model for the disaggregated table.

Paper Eq. 7-8 (following Llop, 2020 and Miller & Blair, 2009):

    p = (I - A')^-1 v          baseline p = 1 for every sector
    HPI^s = sum_i w_i p_i^s,   dHPI^s = HPI^s - 1

A is the *domestic* technical-coefficient matrix (DOM_* rows / industry
columns) and v the per-unit cost of everything else: imported inputs, product
taxes and value added. Electricity tariff shocks are exogenous prices
p_k = 1 + delta_k for the shocked technologies. Prices of the remaining
(endogenous) sectors n then solve

    p_n' = p_n' A_nn + p_x' A_xn + v_n'
  => p_n  = (I - A_nn')^-1 (A_xn' p_x + v_n)

Imported inputs are priced at 1 (optionally shocked), value added and taxes are
held at their 2022 per-unit values, so the result is an upper bound (fixed
technical coefficients, no substitution), as in the paper.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from projection import ACCOUNT_ROWS, table_layout

# Table C5: tariff shocks (%), by country and scenario. Hydropower is never shocked.
# Keys: tech group -> shock; the groups are mapped to GTAP-Power activities below.
TARIFF_SHOCKS_PCT = {
    "EST": {"P1": {"wind": 0, "solar": 0, "other": 15},
            "P2": {"wind": 10, "solar": 5, "other": 0},
            "P3": {"wind": 10, "solar": 5, "other": 15},
            "P4": {"wind": -10, "solar": -5, "other": 15}},
    "LVA": {"P1": {"wind": 0, "solar": 0, "other": 10},
            "P2": {"wind": 8, "solar": 5, "other": 0},
            "P3": {"wind": 8, "solar": 5, "other": 10},
            "P4": {"wind": -8, "solar": -5, "other": 10}},
    "LTU": {"P1": {"wind": 0, "solar": 0, "other": 15},
            "P2": {"wind": 10, "solar": 7, "other": 0},
            "P3": {"wind": 10, "solar": 7, "other": 15},
            "P4": {"wind": -10, "solar": -7, "other": 15}},
}

# GTAP-Power activity -> paper tariff group. GDT (gas, steam, A/C) is not shocked.
TECH_GROUP = {
    "WindBL": "wind",
    "SolarP": "solar",
    "HydroBL": "hydro", "HydroP": "hydro",
    "TnD": "other", "NuclearBL": "other", "CoalBL": "other", "GasBL": "other",
    "OilBL": "other", "OtherBL": "other", "GasP": "other", "OilP": "other",
}


def tariff_shocks(country_code: str, scenario: str, techs) -> dict:
    """Table C5 shock (as a fraction) for each technology present in `techs`."""
    pct = TARIFF_SHOCKS_PCT[country_code][scenario]
    return {t: pct.get(TECH_GROUP[t], 0.0) / 100.0 for t in techs if t in TECH_GROUP}


class PriceModel:
    """Price model built from one balanced disaggregated IO table."""

    def __init__(self, table: pd.DataFrame):
        self.table = table
        self.techs, self.industry_cols, self.fd_cols = table_layout(table)

        # Supplier rows that correspond to an industry (square block).
        self.sectors = [c for c in self.industry_cols if f"DOM_{c}" in table.index]
        missing = [c for c in self.industry_cols if c not in self.sectors]
        if missing:
            raise ValueError(f"Industry columns without a DOM_ row: {missing}")

        x = table.loc["OUTPUT", self.sectors].to_numpy(float)
        if np.any(x <= 0):
            zero = [s for s, v in zip(self.sectors, x) if v <= 0]
            print(f"Price model: zero-output sectors {zero} are held at price 1.")
        self.x = x
        safe = np.where(x > 0, x, 1.0)

        dom = table.loc[[f"DOM_{s}" for s in self.sectors], self.sectors].to_numpy(float)
        self.A = dom / safe[None, :]                      # domestic coefficients a_ij
        self.A[:, x <= 0] = 0.0

        imp_rows = [r for r in table.index if r.startswith("IMP_") and r != "IMP_OTHER"]
        self.imp_coef = pd.Series(
            table.loc[imp_rows, self.sectors].sum(axis=0).to_numpy(float) / safe,
            index=self.sectors,
        )
        # v = everything that is not a domestic intermediate input.
        self.v = 1.0 - self.A.sum(axis=0)
        self.v[x <= 0] = 1.0
        self.va_tax_coef = pd.Series(self.v, index=self.sectors) - self.imp_coef

    # ------------------------------------------------------------------
    def solve(self, shocks: dict | None = None, import_price_shock: float = 0.0,
              cost_shocks: dict | None = None) -> pd.Series:
        """
        shocks            : {sector: delta}, exogenous price p_k = 1 + delta.
        import_price_shock: proportional change in the price of all imported inputs.
        cost_shocks       : {sector: delta} proportional change in that sector's
                            value-added/tax cost per unit (endogenous sectors).
        """
        shocks = shocks or {}
        unknown = [k for k in shocks if k not in self.sectors]
        if unknown:
            raise KeyError(f"Shocked sectors not in the table: {unknown}")

        n = len(self.sectors)
        v = self.v.copy()
        v = v + self.imp_coef.to_numpy() * import_price_shock
        if cost_shocks:
            for k, d in cost_shocks.items():
                v[self.sectors.index(k)] += self.va_tax_coef[k] * d

        p = np.ones(n)
        exo = np.array([s in shocks for s in self.sectors])
        for s, d in shocks.items():
            p[self.sectors.index(s)] = 1.0 + d

        endo = ~exo
        A_nn = self.A[np.ix_(endo, endo)]
        A_xn = self.A[np.ix_(exo, endo)]
        rhs = A_xn.T @ p[exo] + v[endo]
        p[endo] = np.linalg.solve(np.eye(endo.sum()) - A_nn.T, rhs)
        return pd.Series(p, index=self.sectors, name="price")

    # ------------------------------------------------------------------
    def household_weights(self, hh_col: str = "HFCE", hh_energy: dict | None = None) -> pd.Series:
        """
        Household expenditure shares w_i over the sectors.

        Domestic and imported purchases by sector i are pooled (imports priced
        at 1). Optionally replace the household spending on electricity and gas
        / heat with residential energy-consumption values (paper Table B4,
        converted to the table's currency and units):

            hh_energy = {"electricity": value, "gas_steam": value}

        The electricity value is spread over the generating technologies in
        proportion to their current household sales; gas_steam goes to GDT.
        """
        if hh_col not in self.table.columns:
            raise KeyError(
                f"Household column {hh_col!r} not in the table. Final-demand columns: "
                f"{self.fd_cols}"
            )
        t = self.table
        spend = pd.Series(0.0, index=self.sectors)
        for s in self.sectors:
            spend[s] = t.at[f"DOM_{s}", hh_col]
            if f"IMP_{s}" in t.index:
                spend[s] += t.at[f"IMP_{s}", hh_col]
        # Imported inputs not tied to a sector (IMP_D residual, IMP_OTHER) stay out of the index.

        if hh_energy:
            gen = [s for s in self.techs if s != "GDT"]
            if "electricity" in hh_energy and gen:
                base = spend[gen]
                shares = base / base.sum() if base.sum() > 0 else pd.Series(1 / len(gen), index=gen)
                spend[gen] = hh_energy["electricity"] * shares
            if "gas_steam" in hh_energy and "GDT" in spend.index:
                spend["GDT"] = hh_energy["gas_steam"]

        return spend / spend.sum()

    def household_price_index(self, prices: pd.Series, weights: pd.Series):
        """Eq. 8. Returns (HPI, dHPI, direct_effect, indirect_effect)."""
        dp = prices - 1.0
        elec = [s for s in self.techs if s != "GDT" and s in prices.index]   # generating technologies
        direct = float((weights[elec] * dp[elec]).sum())
        total = float((weights * dp).sum())
        return 1.0 + total, total, direct, total - direct


def run_price_scenarios(table: pd.DataFrame, country_code: str,
                        scenarios=("P0", "P1", "P2", "P3", "P4"),
                        hh_col: str = "HFCE", hh_energy: dict | None = None):
    """Price-model results for the paper's baseline and P1-P4 tariff scenarios."""
    model = PriceModel(table)
    w = model.household_weights(hh_col, hh_energy)
    prices, rows = {}, []
    for sc in scenarios:
        shocks = {} if sc == "P0" else tariff_shocks(country_code, sc, model.techs)
        p = model.solve(shocks=shocks)
        hpi, d, direct, indirect = model.household_price_index(p, w)
        prices[sc] = p
        rows.append({"scenario": sc, "HPI": hpi, "dHPI_%": d * 100,
                     "direct_%": direct * 100, "indirect_%": indirect * 100})
    return pd.DataFrame(prices), pd.DataFrame(rows).set_index("scenario")
