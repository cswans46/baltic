"""
End-to-end check using the repository's own functions on a synthetic OECD-style
table (no GAMS / GTAP / OECD files needed):

  disaggregate_electricity -> create_import_alternatives -> RAS (both modes,
  all blends) -> project_and_rebalance -> emissions -> price model.

Run from this folder:  python test_pipeline_integration.py
"""
import sys
import types

import numpy as np
import pandas as pd

try:                                   # gams is only needed by shares_gams at import time
    import gams.transfer  # noqa: F401
except ImportError:
    gt = types.ModuleType("gams.transfer")
    g = types.ModuleType("gams")
    g.transfer = gt
    sys.modules["gams"], sys.modules["gams.transfer"] = g, gt

import matplotlib
matplotlib.use("Agg")

import application as app
from shares_gams import ACTIVITIES, FACTOR_INPUTS
from import_shares import create_import_alternatives, FINAL_DEMAND_COLUMNS
import projection as P
from price_model import run_price_scenarios

rng = np.random.default_rng(7)
CC = "LVA"
SECT = ["A01", "C10T12", "C301", "F", "G", "J62_63", "K", "O", "D"]
FD = FINAL_DEMAND_COLUMNS            # HFCE NPISH GGFC GFCF INVNT DPABR CONS_NONRES EXPO
n = len(SECT)

# ---------------------------------------------------------------- OECD table
A = rng.uniform(0.0, 0.07, (n, n))
A = A / A.sum(axis=0) * rng.uniform(0.35, 0.55, n)
Y = rng.uniform(5, 120, (n, len(FD)))
Y[:, FD.index("INVNT")] = rng.uniform(-6, 10, n)       # negative inventory changes
Y[SECT.index("D"), :] *= 0.4
x = np.linalg.solve(np.eye(n) - A, Y.sum(axis=1))
Z = A * x[None, :]
other = x * (1 - A.sum(axis=0))
imp, tax = other * 0.35, other * 0.04
va = other - imp - tax

rows = ([f"DOM_{s}" for s in SECT] + [f"IMP_{s}" for s in SECT]
        + ["IMP_OTHER", "TXS_INT_FNL", "VALU", "TTL_INT_FNL", "OUTPUT"])
io = pd.DataFrame(0.0, index=rows, columns=SECT + FD + ["TOTAL"])
io.loc[[f"DOM_{s}" for s in SECT], SECT] = Z
io.loc[[f"DOM_{s}" for s in SECT], FD] = Y
# imports by purchasing sector sit on the matching IMP_ rows (diagonal-ish spread)
w = rng.dirichlet(np.ones(n), size=n).T                 # n x n, columns sum to 1
io.loc[[f"IMP_{s}" for s in SECT], SECT] = w * imp[None, :]
io.loc["IMP_D", FD] = [3.0, 0, 1.0, 0, 0, 0, 0, 2.0]    # imported electricity to final demand
io.loc["TXS_INT_FNL", SECT] = tax
io.loc["VALU", SECT] = va
parts = [r for r in rows if r not in ("TTL_INT_FNL", "OUTPUT", "VALU")]
io.loc["TTL_INT_FNL", SECT] = io.loc[parts, SECT].sum()
io.loc["OUTPUT", SECT] = io.loc["TTL_INT_FNL", SECT] + io.loc["VALU", SECT]
body = [r for r in rows if r not in ("TTL_INT_FNL", "OUTPUT")]
io.loc[body, "TOTAL"] = io.loc[body, SECT + FD].sum(axis=1)
io["TOTAL"] = io["TOTAL"].round(3)
io = io.round(3)

# ------------------------------------------------- synthetic GTAP-side inputs
active = ["GDT", "TnD", "GasBL", "WindBL", "HydroBL", "OtherBL", "SolarP"]
sec_wo_D = [s for s in SECT if s != "D"]


def shares(cols):
    m = pd.DataFrame(0.0, index=ACTIVITIES, columns=cols)
    for c in cols:
        m.loc[active, c] = rng.dirichlet(np.ones(len(active)))
    return m


Rm = shares(sec_wo_D)
row_frames = {t: pd.DataFrame({"OECD": sec_wo_D, CC: Rm.loc[t, sec_wo_D].to_numpy()})
              for t in ACTIVITIES}
inputs = sec_wo_D + FACTOR_INPUTS
level_frames, raw_frames = {}, {}
for t in ACTIVITIES:
    lv = rng.uniform(0, 5, len(inputs)) * (t in active)
    level_frames[t] = pd.DataFrame({"VDFB": inputs, CC: lv})
    e = pd.Series(0.0, index=ACTIVITIES)
    if t in active:
        e[active] = rng.uniform(0, 2, len(active))
    raw_frames[t] = pd.DataFrame({"VDFB": e.index, CC: e.to_numpy()})

# --------------------------------------------- 1. electricity disaggregation
dis = app.disaggregate_electricity(io, CC, row_frames, level_frames, raw_frames)

# --------------------------------------------- 2. import alternatives (GTAP)
vm_cols = sec_wo_D + ["D_Gas", "D_Wind", "D_Solar", "D_Hydro", "D_Other"]
vmfb = shares(vm_cols)
_, gtap = create_import_alternatives(io=dis, eia_weights=None, vmfb_shares=vmfb)

# --------------------------------------------- 3. RAS, every mode / blend
ras_out = {}
for mode in ("intermediate", "full"):
    for tag, blend in app.RAS_SENSITIVITIES.items():
        ras_out[(mode, tag)] = (app.RAS(gtap, f"{CC} {mode} {tag}", mode=mode, blend=blend), blend)

# --------------------------------------------- 4. project + re-RAS
techs, ind_cols, fd_cols = P.table_layout(gtap)
print("\nlayout: techs", techs, "\n        fd cols", fd_cols)
assert set(fd_cols) == set(FD), fd_cols

for (mode, tag), (bal, blend) in ras_out.items():
    proj, info = P.project_and_rebalance(bal, f"{CC} {mode} {tag} 2030", CC, ras_balance=app._ras_balance, mode=mode, blend=blend)
    ratio = proj.loc["OUTPUT", ind_cols] / bal.loc["OUTPUT", ind_cols].replace(0, np.nan)
    ok = ratio.dropna()
    assert np.allclose(ok, info["g_col"][ok.index]), (mode, tag)
    assert not proj.isna().any().any()
    # accounting identity still holds in every industry column
    body_ = [r for r in proj.index if r not in ("TTL_INT_FNL", "OUTPUT")]
    assert np.allclose(proj.loc[body_, ind_cols].sum(), proj.loc["OUTPUT", ind_cols])
    # negative inventory cells keep their sign
    neg0 = bal.loc[body_, "INVNT"] < 0
    assert (proj.loc[body_, "INVNT"][neg0] <= 1e-9).all()

    # emissions on a toy extension (intensity constant)
    ext = pd.DataFrame(rng.uniform(1, 9, (2, len(ind_cols))), index=["CO2", "CH4"], columns=ind_cols)
    e1, gr = P.project_emissions(ext, bal, proj)
    nz = [c for c in ind_cols if bal.at["OUTPUT", c] > 0]
    assert np.allclose(e1[nz].div(proj.loc["OUTPUT", nz]), ext[nz].div(bal.loc["OUTPUT", nz]))

    # price model on both years
    for t in (bal, proj):
        _, s = run_price_scenarios(t, CC)
        assert abs(s.loc["P0", "dHPI_%"]) < 1e-9
print("\nINTEGRATION TEST PASSED")

# ------------------------------------------------ 5. run_projection wiring
import tempfile
from pathlib import Path
from run_projection import run_projection

with tempfile.TemporaryDirectory() as tmp:
    rebal = {"Latvia": {f"GTAP_{m}_{t}": v[0] for (m, t), v in ras_out.items()}}
    non_elec = [c for c in ind_cols if c not in techs]
    ext = pd.DataFrame(rng.uniform(1, 9, (3, len(non_elec))), index=["CO2", "nonCO2", "GHG"],
                       columns=non_elec)
    ext.loc["GHG"] = ext.loc["CO2"] + ext.loc["nonCO2"]
    ext.to_csv(Path(tmp) / "emissions_LVA.csv")
    d35 = pd.DataFrame({"CO2_tonnes": rng.uniform(1, 9, len(techs)),
                        "nonCO2_tCO2e": rng.uniform(0, 1, len(techs))},
                       index=pd.Index(techs, name="GTAP_activity"))
    d35["GHG_tCO2e"] = d35.sum(axis=1)
    d35.to_csv(Path(tmp) / "LVA2023_AEA_D35_GTAP_emissions.csv")
    res = run_projection(rebal, {"Latvia": "LVA"}, "2022", tmp, ras_balance=app._ras_balance,
                         ras_sensitivities=app.RAS_SENSITIVITIES,
                         emissions_file=str(Path(tmp) / "emissions.csv"))
    files = sorted(p.name for p in Path(tmp).iterdir())
    assert len(res["Latvia"]) == 6
    assert sum(f.endswith("_disaggregated_2030BAU.csv") for f in files) == 6
    assert sum(f.endswith("_emissions_2030BAU.csv") for f in files) == 6
    e22 = pd.read_csv(Path(tmp) / "LVA2022_gtap_full_baseline_emissions_2022.csv", index_col=0)
    e30 = pd.read_csv(Path(tmp) / "LVA2022_gtap_full_baseline_emissions_2030BAU.csv", index_col=0)
    g = res["Latvia"]["GTAP_full_baseline"].loc["OUTPUT", e22.columns] / \
        rebal["Latvia"]["GTAP_full_baseline"].loc["OUTPUT", e22.columns].replace(0, np.nan)
    nz = g.dropna().index
    assert np.allclose(e30[nz] / e22[nz], g[nz].to_numpy()[None, :])
    assert np.allclose(e30.loc["CO2"] + e30.loc["nonCO2"], e30.loc["GHG"])  # GHG stays CO2 + nonCO2
    assert sum("price_model" in f for f in files) == 12
    # bad household column must warn, not crash, and still write the projection
    res = run_projection(rebal, {"Latvia": "LVA"}, "2022", tmp, ras_balance=app._ras_balance,
                         ras_sensitivities=app.RAS_SENSITIVITIES, household_column="NOPE")
    assert len(res["Latvia"]) == 6
print("run_projection wiring OK")

# unmapped labels are reported together
bad = gtap.copy()
bad = bad.rename(columns={"K": "ZZ1"}, index={"DOM_K": "DOM_ZZ1", "IMP_K": "IMP_ZZ1"})
try:
    P.growth_vectors(bad, CC)
    raise SystemExit("expected KeyError")
except KeyError as exc:
    assert "ZZ1" in str(exc)
print("unmapped-label error OK")
