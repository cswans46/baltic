# JRC and GTAP allocation for Baltic electricity / heat generation

The application.py workflow now reads country-specific JRC-IDEES 2023 workbooks and allocates the OECD D35 emissions total to the 11 GTAP generating activities.

## Inputs

For each country folder (Estonia, Latvia, Lithuania), the script searches recursively for:

- *PowerGen*.xlsx — JRC-IDEES 2023 power generation.
- *EmissionBalance*.xlsx — JRC-IDEES energy / emissions balance.
- OECD {country} 2022 emissions.xlsx — OECD Air Emissions Accounts workbook.

The GTAP GDX defaults are:

- ../GDX12aPower23/GSDFEMISS.GDX — CO2.
- ../GDX12aPower23/GSDFNCO2.GDX — non-CO2 and AR6 GWP.

Paths and workbook filename patterns can be overridden using the command-line arguments in application.py --help.

## Allocation logic

### IO output allocation

- JRC net electricity generation is aggregated to coal, gas, oil, other, wind, solar, hydro and nuclear.
- The 11 GTAP generation activities are created from those broad JRC groups.
- For gas, oil and hydro, base/peak splits use GTAP activity output weights calculated from the zero-profit input-cost identity (intermediate inputs plus endowment inputs).
- In the IO supply row, the JRC shares split the GTAP generating-activity subtotal. The existing GTAP split between generating activities and non-generating activities (T&D and gas/steam distribution) is preserved.
- Purchaser-specific generation-vs-non-generation totals are retained; the JRC mix replaces the composition within the 11 generating activities.

### OECD D35 emissions allocation

- **CO2:** fuel-specific JRC CO2 emissions from conventional thermal power plants plus heat-only plants determine broad fuel-group shares. Biomass CO2 is excluded, consistent with the OECD AEA GHG account. GTAP MDF and MMF records split gas, oil and hydro between base and peak activities.
- **Non-CO2:** JRC combined electricity, CHP heat and heat-only output by fuel provides the broad-group shares. GTAP EMI_IO records are converted to CO2-equivalent using the country-specific GWP symbol for AR6; CO2 is excluded from this account. These GTAP values split gas, oil and hydro base/peak activities.
- The resulting activity allocations are scaled to the OECD AEA D35 CO2 and non-CO2 totals separately. The script checks that both totals are conserved.

The JRC PowerGen CO2 fuel totals are cross-checked against TI_EHG_E in the EmissionBalance workbook before emissions are allocated.

## Outputs

Written under Disaggregated IO:

- {country_code}2023_JRC_generation_shares.csv
- {country_code}2023_AEA_D35_GTAP_emissions.csv
- Existing disaggregated / rebalanced IO outputs.

The emissions CSV contains the JRC broad-group shares, GTAP CO2 and non-CO2 shares, allocated CO2 tonnes, allocated non-CO2 tonnes CO2e, and the OECD AEA totals used for scaling.

## Important year distinction

The workflow deliberately combines **2023 JRC / GTAP generation and emissions weights** with the **2022 OECD AEA D35 emissions total** supplied in the country workbook. The IO table year remains controlled by --year (default 2022).

The GDX-reading path follows the GAMS Python API and the GTAP symbol dimensional structure inspected in the Datacenters repository. The complete workflow still needs to be run in the user's local environment with the actual GTAP GDX files and all three countries' workbooks; those GDX binaries are not present in this repository branch.
