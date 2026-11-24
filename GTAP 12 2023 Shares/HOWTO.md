# How-to guide: Baltic disaggregated IO tables, 2030 projection, emissions and price model

This guide covers running `application.py` (via `run.bat`), what it produces, how to
change what it produces, and how the 2030 BAU projection, environmental extension and
price model work. For the electricity/heat emissions allocation itself see
`README_JRC_allocation.md`.

## 1. What the pipeline does

`application.py` runs these steps for Estonia, Latvia and Lithuania:

1. Read the OECD domestic IO tables (`<code><year>dom.csv`).
2. Calculate domestic and import allocation shares from GTAP (`shares_gams.py`).
3. Disaggregate the OECD electricity sector (D) into the 12 GTAP generating activities
   plus `GDT` (district heat), using the JRC-IDEES generation mix, then split the
   electricity imports (`IMP_D` row) across those activities. Two import alternatives
   exist: **GTAP/VMFB** (all countries) and **EIA** (Estonia and Lithuania only).
4. Rebalance the 2022 table with RAS.
5. Export the balanced 2022 table.
6. **Project** the balanced table to 2030 (BAU), **rebalance it with RAS again**, grow the
   environmental extension, and run the **price model** on both years.

## 2. Folder layout and inputs

Everything is relative to `--io_database_root` (default is set at the top of
`application.py`; override it on the command line).

| Input | Location |
|---|---|
| OECD domestic IO table | `<root>\<Country>\1. OECD Tables\2. Domestic output & imports\<code><year>dom.csv` |
| EIA import workbook (Estonia, Lithuania) | anywhere under `<root>\<Country>\`, named `<Country> - import disaggregation .xlsx` |
| JRC-IDEES PowerGen and EmissionBalance workbooks | under `<root>\<Country>\` (patterns `--jrc_powergen_pattern`, `--jrc_emission_balance_pattern`) |
| OECD AEA emissions workbook | under `<root>\<Country>\`, `OECD <Country> 2022 emissions.xlsx` |
| GTAP GDX files | `..\GDX12aPower23\GSDFEMISS.GDX` and `GSDFNCO2.GDX` (relative to the code folder) |
| GAMS | `C:\GAMS\51` (`--gams_system_directory`) |

All outputs go to `<root>\Disaggregated IO`. The code only **writes** to that folder
(it is created if missing); it is never read as an input.

## 3. Running it

`run.bat` calls `python application.py` with default settings. Add options to that line.

| Option | Default | Meaning |
|---|---|---|
| `--ras_modes` | `full` | RAS modes to run: `full`, `intermediate`, or `"full intermediate"` |
| `--ras_sensitivities` | `baseline` | From `baseline`, `sensitivity_10pct`, `sensitivity_25pct` (space-separated) |
| `--import_alternatives` | `preferred` | `preferred` = EIA where an EIA workbook exists (Estonia, Lithuania), otherwise GTAP (Latvia). Or `EIA`, `GTAP`, `all` |
| `--keep_diagnostics` | off | Also write the unbalanced import-alternative CSVs, JRC generation shares, RAS convergence plots and before/after comparison files |
| `--skip_projection` | off | Stop after the 2022 tables (skip step 6) |
| `--emissions_file` | none | Optional emissions for the non-electricity sectors (section 7) |
| `--household_column` | `HFCE` | Final-demand column holding household consumption (price model) |
| `--countries_to_share` | `Latvia Lithuania Estonia` | Countries to run, by name, space-separated |
| `--drop_inactive` | off | Omit technologies with no activity in a country (default keeps all 13 columns) |

Example (everything, as before the cleanup):

```
python application.py --ras_modes "full intermediate" --ras_sensitivities "baseline sensitivity_10pct sensitivity_25pct" --import_alternatives all --keep_diagnostics
```

### What the options mean

* **full vs intermediate RAS.** `intermediate` balances only the intermediate-use block
  (supplier rows by industry columns), keeping value added and final demand fixed. `full`
  balances the whole table, including value added and final demand.
* **Sensitivities.** `baseline` keeps the original allocation as the RAS seed; the 10% and
  25% variants blend in a proportional seed.
* **EIA vs GTAP imports.** Both split `IMP_D` across the generating activities. EIA weights
  set each purchaser's imported electricity across five categories (Gas, Wind, Solar,
  Hydro, Other). GTAP/VMFB shares then split only *within* a category: Hydro between
  `HydroBL` and `HydroP`, and Other across the remaining technologies. Gas maps directly
  to `GDT`. GTAP/VMFB applies the VMFB shares directly. Latvia has no EIA workbook, so it
  always uses GTAP.

## 4. Output files (default settings)

For each country, with `<code>` = `EST`, `LVA`, `LTU`, `<year>` = `--year` (default 2022),
and `<alt>` = `eia` (EST, LTU) or `gtap` (LVA):

| File | Contents |
|---|---|
| `<code><year>_<alt>_full_baseline_disaggregated.csv` | Balanced 2022 table |
| `<code><year>_<alt>_full_baseline_disaggregated_2030BAU.csv` | Projected and rebalanced 2030 BAU table |
| `<code>2023_AEA_D35_GTAP_emissions.csv` | Electricity/heat emissions allocation (2022 OECD total, 2023 JRC/GTAP weights); input to the emissions projection |
| `<code><year>_<alt>_full_baseline_emissions_2022.csv` | Emissions extension used for the projection |
| `<code><year>_<alt>_full_baseline_emissions_2030BAU.csv` | Emissions grown to 2030 |
| `<code>_<alt>_full_baseline_price_model_2022.csv` | Price-model summary, 2022 table |
| `<code>_<alt>_full_baseline_price_model_2030BAU.csv` | Price-model summary, 2030 BAU table |

The `<year>` in the 2030 file name is the IO table year (2022), not 2030.

## 5. Cleaning out old outputs

Deleting the whole `Disaggregated IO` folder is safe: no input is stored there, and the
next run recreates it with only the files above. Copy anything you want to keep first
(for example earlier sensitivity results), because old files are not regenerated unless you
re-run with the matching options.

## 6. 2030 BAU projection (step 6)

Method, following the paper's BAU scenario:

* Each sector has a cumulative 2022-to-2030 growth factor `g` (**Table C1**, in `growth.py`).
  All 12 generating activities use the electricity factor (Estonia 1.460, Latvia 1.427,
  Lithuania 1.553); `GDT` uses the gas, steam and air-conditioning factor (0.979, 1.104,
  1.126). `growth_factor()` also accepts the OECD codes used in the IO table.
* Every cost entry in a sector's column (domestic and imported inputs, taxes, value added)
  is scaled by that sector's `g`, so 2022 technical, import and value-added coefficients are
  preserved.
* Each final-demand cell is scaled by the `g` of the supplying sector.
* The result no longer balances (rows grow with their buyers, sectors grow with `g`), so RAS
  runs again, in the same mode and blend as the 2022 balancing, to row targets
  `g x (2022 row total)` for domestic supplier rows and column targets
  `g x (2022 column total)`. Projected sector output therefore equals `g x` 2022 output.
  Import and tax rows (or final-demand columns in `full` mode) are the free margin that
  closes the grand total.
* The log prints a **closing factor** for each projection. Values near 1 are normal; a value
  far from 1 means the growth inputs are inconsistent with the table's structure.

To change growth rates, edit `TABLE_C1` in `growth.py`. If the run stops with
`No Table C1 growth factor for these sector labels: [...]`, add those labels to
`TABLE_C1` or `ALIASES`.

## 7. Environmental extension

Emissions follow the paper (Eq. 3 and 4): constant emission intensity per unit of output,
so each sector's emissions grow with its projected output. There is no separate growth
rate for fossil inputs.

* **Electricity and district heat** are read automatically from
  `<code>2023_AEA_D35_GTAP_emissions.csv` (`CO2_tonnes`, `nonCO2_tCO2e`, `GHG_tCO2e`) and
  become the rows `CO2`, `nonCO2` and `GHG` of the technology columns.
* **Other sectors (optional).** Supply `--emissions_file <stem>.csv`; the code reads
  `<stem>_EST.csv`, `<stem>_LVA.csv`, `<stem>_LTU.csv`. Rows must be `CO2`, `nonCO2`, `GHG`
  in tonnes, columns are IO sector codes. An aggregate `D` column is ignored.
* If the emission inputs are missing or inconsistent, the run prints a `WARNING` and
  continues; the projected tables are still written.

## 8. Price model

`price_model.py` implements the paper's cost-push Leontief price model (Eq. 7 and 8):
baseline prices are 1 for every sector; electricity tariff shocks (**Table C5**,
scenarios P1 to P4; hydropower unchanged) are imposed as exogenous prices `1 + shock`
on the technologies; all other sectors' prices follow from fixed technical, import,
tax and value-added coefficients. The output file reports, for P0 (baseline) and P1 to
P4: the household price index, its change in percent, and the direct (electricity) and
indirect parts. Household weights come from the `--household_column` column of the
table (domestic plus imported purchases by sector).

The paper reallocates household electricity, gas and heat spending using residential
energy expenditure (Table B4). This is not part of the automatic run. To use it, call
`run_price_scenarios(table, country_code, hh_energy={"electricity": value, "gas_steam": value})`
with values in the table's currency and units.

## 9. Tests

Run from the code folder; neither needs GTAP, GAMS or the OECD files.

```
python test_projection.py              # growth factors, projection, emissions, price model on a toy table
python test_pipeline_integration.py    # real disaggregation, import split and RAS on a synthetic table,
                                       # then projection, emissions, price model and the output options
```

Both end with a `PASSED` line. `python -m pytest test_jrc_allocation.py` tests the JRC
allocation code.

## 10. Troubleshooting

| Message | Cause and fix |
|---|---|
| `No Table C1 growth factor for these sector labels` | A sector code in the table is not in `growth.TABLE_C1`; add it (or an alias) |
| `Household column 'X' not in the table` (price model warning) | Set `--household_column` to a final-demand column that exists |
| `Import alternative 'EIA' is not available here` | `--import_alternatives EIA` was used for Latvia; use `preferred` or `all` |
| `--ras_modes must be ...` / `--ras_sensitivities must be ...` | Misspelled value; see section 3 |
| `cannot close grand total ...` | The projected margins cannot be reconciled; check the growth inputs and the table's import and tax rows |
| `emissions allocated to [...] are dropped` | An activity with emissions is not a column of the table (for example removed by `--drop_inactive`) |
