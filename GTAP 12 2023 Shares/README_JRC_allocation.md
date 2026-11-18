# JRC and GTAP allocation for Baltic electricity / heat generation

The application.py workflow reads country-specific JRC-IDEES 2023 workbooks and allocates the OECD D35 emissions total to the 11 GTAP electricity-generating activities plus the GTAP district-heat activity, \`GDT\`.

## Inputs

For each country folder (Estonia, Latvia, Lithuania), the script searches recursively for:

- *PowerGen*.xlsx — JRC-IDEES 2023 power generation.
- *EmissionBalance*.xlsx — JRC-IDEES energy / emissions balance.
- OECD {country} 2022 emissions.xlsx — OECD Air Emissions Accounts workbook.

The GTAP GDX defaults are:

- ../GDX12aPower23/GSDFEMISS.GDX — CO2.
- ../GDX12aPower23/GSDFNCO2.GDX — non-CO2 and AR5 GWP.

Paths and workbook filename patterns can be overridden using the command-line arguments in application.py --help.

## Allocation logic

### IO output allocation

- JRC net electricity generation is aggregated to coal, gas, oil, other, wind, solar, hydro and nuclear.
- The 11 GTAP electricity-generating activities are created from those broad JRC groups.
- For gas, oil and hydro, base/peak splits use GTAP activity output weights calculated from the zero-profit input-cost identity (intermediate inputs plus endowment inputs).
- In the IO supply row, the JRC shares split the GTAP generating-activity subtotal. The existing GTAP split between generating activities and non-generating activities (T&D and gas/steam distribution) is preserved.
- Purchaser-specific generation-vs-non-generation totals are retained; the JRC mix replaces the composition within the 11 generating activities.
- This output allocation remains electricity-only; \`GDT\` is not added to the 11-activity JRC generation mix.

### OECD D35 emissions allocation

**CO2**

- CO2 is read from the JRC EmissionBalance workbook, whose \`TI_EHG_*\` sheet values are already kt CO2. They are not fuel inputs and are not multiplied by the emission-factor sheet.
- The electricity-only sheet (\`TI_EHG_E_E\`) and CHP sheet (\`TI_EHG_CHP_E\`) are added together and allocated wholly across the 11 GTAP electricity-generating activities. Thus all CHP/cogeneration emissions are assigned to electricity.
- The heat-only sheet (\`TI_EHG_H_E\`) is allocated to GTAP \`GDT\`, the district-heat activity.
- Within the electricity allocation, the JRC fuel-specific fossil/non-biogenic CO2 values determine broad fuel-group shares. GTAP MDF/MMF activity emissions split the gas, oil and hydro groups between base and peak activities.
- Renewable/biofuel CO2 (\`RA000\`) is excluded, consistent with the intended fossil/non-biogenic CO2 basis. Non-renewable waste remains in the \`other\` group. The parser uses parent fuel-category rows only, avoiding double counting of child rows, and checks the selected groups against each sheet's \`TOTAL - RA000\`.

**Non-CO2**

- GTAP \`EMI_IO\` records determine activity-level non-CO2 shares directly. CO2 is excluded, and the remaining gases are converted to CO2-equivalent using the country-specific AR5 GWP records.
- The non-CO2 allocation includes the 11 electricity-generating activities and \`GDT\`; it does not use JRC PowerGen fuel-input shares as a proxy.

**OECD totals and checks**

- OECD AEA D35 remains the source of aggregate CO2 and non-CO2 totals. Non-CO2 is calculated as AEA greenhouse gases minus AEA CO2.
- CO2 and non-CO2 totals are allocated separately, and the code checks that both OECD totals are conserved.
- The JRC EmissionBalance totals provide allocation weights only; they do not replace the OECD AEA totals.

## Outputs

Written under Disaggregated IO:

- {country_code}2023_JRC_generation_shares.csv
- {country_code}2023_AEA_D35_GTAP_emissions.csv
- Existing disaggregated / rebalanced IO outputs.

The emissions CSV includes all 12 activities (the 11 electricity generators plus \`GDT\`), the JRC electricity and heat-only CO2 diagnostics, GTAP activity-level CO2 and non-CO2 weights, allocated CO2 tonnes, allocated non-CO2 tonnes CO2e, and the OECD AEA totals used for scaling.

## Important year distinction

The workflow deliberately combines **2023 JRC / GTAP generation and emissions weights** with the **2022 OECD AEA D35 emissions total** supplied in the country workbook. The IO table year remains controlled by --year (default 2022).

The GDX-reading path follows the GAMS Python API and the GTAP symbol dimensional structure. The complete workflow still needs to be run in the user's local environment with the actual GTAP GDX files and all three countries' workbooks; those GDX binaries are not present in this repository branch.
