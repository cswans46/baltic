import sys, types
import numpy as np, pandas as pd

# gams is not installed in the sandbox; shares_gams only needs it at import time.
gt = types.ModuleType("gams.transfer"); g = types.ModuleType("gams"); g.transfer = gt
sys.modules["gams"] = g; sys.modules["gams.transfer"] = gt
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import projection as P
from application import _ras_balance as _rb
from price_model import PriceModel, run_price_scenarios, tariff_shocks
from growth import growth_factor

rng = np.random.default_rng(1)
ind = ["A01", "C10T12", "F", "G", "K", "O", "GDT", "TnD", "WindBL", "SolarP", "HydroBL"]
n = len(ind)
fd = ["HFCE", "GGFC", "GFCF", "EXPO"]

A = rng.uniform(0, 0.08, (n, n))
A[[ind.index(t) for t in ind[7:]], :] *= 0.2             # electricity sold mainly to others, little input
for t in ind[7:]:
    A[:, ind.index(t)] *= 1.5
colsum_target = rng.uniform(0.35, 0.6, n)                # domestic share of cost
A = A / A.sum(axis=0) * colsum_target
y = rng.uniform(20, 200, (n, len(fd)))
x = np.linalg.solve(np.eye(n) - A, y.sum(axis=1))
Z = A * x[None, :]
cost_other = x * (1 - A.sum(axis=0))
imp = cost_other * 0.35
tax = cost_other * 0.05
va = cost_other - imp - tax

rows = [f"DOM_{s}" for s in ind] + ["IMP_GDT", "IMP_D", "IMP_OTHER", "TXS_INT_FNL", "VALU", "TTL_INT_FNL", "OUTPUT"]
cols = ind + fd + ["TOTAL"]
T = pd.DataFrame(0.0, index=rows, columns=cols)
T.loc[[f"DOM_{s}" for s in ind], ind] = Z
T.loc[[f"DOM_{s}" for s in ind], fd] = y
T.loc["IMP_GDT", ind] = imp * 0.5
T.loc["IMP_D", ind] = imp * 0.5
T.loc["IMP_OTHER", ind] = 0.0
T.loc["TXS_INT_FNL", ind] = tax
T.loc["VALU", ind] = va
body = [r for r in rows if r not in ("TTL_INT_FNL", "OUTPUT", "VALU")]
T.loc["TTL_INT_FNL", ind] = T.loc[body, ind].sum()
T.loc["OUTPUT", ind] = T.loc["TTL_INT_FNL", ind] + T.loc["VALU", ind]
T.loc["IMP_D", "HFCE"] = 5.0
T.loc[[r for r in rows if r not in ("TTL_INT_FNL", "OUTPUT")], "TOTAL"] = \
    T.loc[[r for r in rows if r not in ("TTL_INT_FNL", "OUTPUT")], ind + fd].sum(axis=1)

# sanity: DOM row totals ~ output (fully consistent toy economy)
print("toy table: max |DOM row total - OUTPUT| =",
      np.abs(T.loc[[f"DOM_{s}" for s in ind], "TOTAL"].to_numpy() - T.loc["OUTPUT", ind].to_numpy()).max())

for cc in ("EST", "LVA", "LTU"):
    for mode in ("intermediate", "full"):
        for blend in (0.0, 0.25):
            out, info = P.project_and_rebalance(T, f"toy {cc} {mode} {blend}", cc, ras_balance=_rb, mode=mode, blend=blend)
            gc = info["g_col"]
            real = out.loc["OUTPUT", ind] / T.loc["OUTPUT", ind]
            assert np.allclose(real, gc), (cc, mode)
            # coefficients preserved on VALU
            if mode == "intermediate":   # VALU sits outside the RAS block
                assert np.allclose(out.loc["VALU", ind] / out.loc["OUTPUT", ind],
                                   T.loc["VALU", ind] / T.loc["OUTPUT", ind])
            assert (out.loc[[r for r in out.index if r not in ("TTL_INT_FNL", "OUTPUT")], ind + fd].to_numpy() >= -1e-9).all()

# growth factors pulled from Table C1
assert growth_factor("WindBL", "EST") == 1.460 and growth_factor("GDT", "LTU") == 1.126
assert growth_factor("E", "EST") == 0.979 and growth_factor("E", "LVA") == 1.104   # Table C1 water supply
assert growth_factor("C301", "LVA") == 1.060 and growth_factor("J62_63", "LTU") == 1.200
print("growth factors OK")

# emissions grow with output
ext = pd.DataFrame({c: rng.uniform(1, 100, 2) for c in ind + ["HFCE"]}, index=["CO2", "CH4"])
out, _ = P.project_and_rebalance(T, "toy", "EST", ras_balance=_rb, mode="full")
e1, gr = P.project_emissions(ext, T, out)
print(gr.round(3).to_dict())
assert np.allclose(e1[ind] / ext[ind], out.loc["OUTPUT", ind] / T.loc["OUTPUT", ind])
assert np.allclose(e1[ind].div(out.loc["OUTPUT", ind]), ext[ind].div(T.loc["OUTPUT", ind]))  # intensity constant
print("emissions OK")

# price model
m = PriceModel(T)
p0 = m.solve()
assert np.allclose(p0, 1.0), "baseline prices must be 1"
w = m.household_weights("HFCE")
assert abs(w.sum() - 1) < 1e-12
pr, summ = run_price_scenarios(T, "EST")
print(summ.round(4))
assert abs(summ.loc["P0", "dHPI_%"]) < 1e-9
assert summ.loc["P1", "dHPI_%"] > 0 and summ.loc["P4", "dHPI_%"] < summ.loc["P1", "dHPI_%"]
# exogenous sectors sit exactly at their shocked price
sh = tariff_shocks("EST", "P3", m.techs)
p3 = m.solve(shocks=sh)
assert all(abs(p3[k] - (1 + d)) < 1e-12 for k, d in sh.items())
# pure import shock: with import coefficient only, price rise bounded by import cost share
pi = m.solve(import_price_shock=0.10)
assert (pi >= 1).all() and (pi - 1 <= 0.10 + 1e-12).all()
# energy reallocation
w2 = m.household_weights("HFCE", {"electricity": 80.0, "gas_steam": 10.0})
assert abs(w2.sum() - 1) < 1e-12
_, s2 = run_price_scenarios(T, "EST", hh_energy={"electricity": 80.0, "gas_steam": 10.0})
print(s2.round(4))
print("ALL TESTS PASSED")
