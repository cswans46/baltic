
# imports_disaggregation.py
#
# Import-side electricity disaggregation for the Baltic IO tables.
#
# Produces two alternatives:
#   1. EIA: EIA five-category import weights, expanded to 13 GTAP activities
#           using within-category GTAP VMFB proportions.
#   2. GTAP: GTAP VMFB import shares directly.
#
# The original IMP_D row is replaced by IMP_<activity> rows.
# Domestic rows (DOM_*) and domestic allocation are not modified here.
#
# Expected orientations:
#   eia_weights:
#       index   = EIA purchasing-sector row identifiers (e.g. TTL_01, TTL_35: Wind)
#       columns = Gas, Wind, Solar, Hydro, Other
#
#   vmfb_shares:
#       index   = the 13 GTAP activities
#       columns = purchasing-sector codes in the IO table
#       values  = import shares, summing to one down each column
#
#   io:
#       rows    = commodity supply rows, including IMP_D
#       columns = purchasing sectors and final-demand columns, optionally TOTAL
#
# The caller must supply purchaser_to_eia where IO column names do not
# directly match the EIA workbook row identifiers.

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

import numpy as np
import pandas as pd


ACTIVITIES = [
    "GDT",
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
    "SolarP",
]

EIA_CATEGORIES = ["Gas", "Wind", "Solar", "Hydro", "Other"]

ACTIVITY_TO_EIA = {
    "GDT": "Gas",
    "TnD": "Other",
    "NuclearBL": "Other",
    "CoalBL": "Other",
    "GasBL": "Other",
    "WindBL": "Wind",
    "HydroBL": "Hydro",
    "OilBL": "Other",
    "OtherBL": "Other",
    "GasP": "Other",
    "HydroP": "Hydro",
    "OilP": "Other",
    "SolarP": "Solar",
}

# Used to select the appropriate EIA TTL_35 supplier row when the
# purchasing column itself is one of the disaggregated electricity activities.
ACTIVITY_TO_TTL35 = {
    "GDT": "Gas, steam and air conditioning supply",
    "WindBL": "Wind",
    "SolarP": "Solar PV",
    "HydroBL": "Hydro",
    "HydroP": "Hydro",
    "TnD": "Other",
    "NuclearBL": "Other",
    "CoalBL": "Other",
    "GasBL": "Other",
    "OilBL": "Other",
    "OtherBL": "Other",
    "GasP": "Other",
    "OilP": "Other",
}

# Disaggregated IO electricity purchaser columns and their EIA categories.
IO_ELECTRICITY_COLUMN_TO_EIA = {
    "D_Gas": "Gas",
    "D_Wind": "Wind",
    "D_Solar": "Solar",
    "D_Hydro": "Hydro",
    "D_Other": "Other",
}

# The original VMFB purchasing-sector column corresponding to each
# disaggregated electricity purchaser column.
IO_ELECTRICITY_COLUMN_TO_VMFB = {
    "D_Gas": "D",
    "D_Wind": "D",
    "D_Solar": "D",
    "D_Hydro": "D",
    "D_Other": "D",
}

# Final-demand columns are not technology-disaggregated because VMFB
# does not identify their technology-specific import composition.
FINAL_DEMAND_COLUMNS = [
    "HFCE",
    "NPISH",
    "GGFC",
    "GFCF",
    "INVNT",
    "DPABR",
    "CONS_NONRES",
    "EXPO",
]

# Map the 13 GTAP electricity purchasing activities in the IO table
# to the five disaggregated electricity purchasing columns in VMFB.
ACTIVITY_TO_VMFB_PURCHASER = {
    "GDT": "D_Gas",
    "WindBL": "D_Wind",
    "SolarP": "D_Solar",
    "HydroBL": "D_Hydro",
    "HydroP": "D_Hydro",
    "TnD": "D_Other",
    "NuclearBL": "D_Other",
    "CoalBL": "D_Other",
    "GasBL": "D_Other",
    "OilBL": "D_Other",
    "OtherBL": "D_Other",
    "GasP": "D_Other",
    "OilP": "D_Other",
}

# The OECD IO "other" column is also retained in the aggregate IMP_D row.
NON_ALLOCATED_COLUMNS = FINAL_DEMAND_COLUMNS + [".."]

def _normalise_text(value: object) -> str:
    """Normalise workbook labels and IO identifiers for matching."""
    text = str(value).strip().lower()
    text = text.replace("&", "and")
    text = text.replace(" ", "")
    text = text.replace("_", "")
    text = text.replace("-", "")
    text = text.replace(".", "")
    text = text.replace(":", "")
    return text


def _category_from_header(value: object) -> Optional[str]:
    """Identify one of the five EIA categories from a workbook header."""
    text = _normalise_text(value)

    if "wind" in text:
        return "Wind"
    if "solar" in text:
        return "Solar"
    if "hydro" in text:
        return "Hydro"
    if "other" in text:
        return "Other"
    if "gas" in text or ("steam" in text and "conditioning" in text):
        return "Gas"

    return None


def load_eia_import_weights(
    workbook_path: str | Path,
    sheet_name: str = "Column weights",
    header_row: int = 0,
    index_col: int = 0,
) -> pd.DataFrame:
    """
    Load normalized EIA import weights.

    Supports workbooks containing:
      - Five raw D35 technology-weight columns.
      - A row total column.
      - Five normalized category-weight columns.

    The normalized columns are selected using their category headers.
    """
    raw = pd.read_excel(
        workbook_path,
        sheet_name=sheet_name,
        header=header_row,
        index_col=index_col,
    )
    
    print(
        "Raw EIA rows containing TTL_07:",
        [
            str(index)
            for index in raw.index
            if "TTL_07" in str(index)
        ],
    )

    selected = {}

    for category in EIA_CATEGORIES:
        matches = [
            col for col in raw.columns
            if _category_from_header(col) == category
        ]

        if not matches:
            raise ValueError(
                f"Could not find a column for EIA category {category!r} "
                f"in sheet {sheet_name!r}. Available columns: "
                f"{list(raw.columns)}"
            )

        # The last matching column is the normalized weight column.
        selected[category] = raw[matches[-1]]

    weights = pd.DataFrame(selected, index=raw.index)

    weights = (
        weights.apply(pd.to_numeric, errors="coerce")
        .fillna(0.0)
    )

    # Remove blank rows and rows with no allocation weights.
    weights = weights.loc[
        weights.abs().sum(axis=1) > 0
    ].copy()

    row_sums = weights.sum(axis=1)

    invalid = ~np.isclose(
        row_sums.to_numpy(),
        1.0,
        atol=1e-5,
        rtol=1e-9,
    )

    if invalid.any():
        bad = {
            str(index): float(row_sums.loc[index])
            for index in row_sums.index[invalid]
        }

        raise ValueError(
            "EIA normalized weights do not sum to one for these rows: "
            f"{bad}"
        )

    return weights


def _find_eia_row(
    eia_weights: pd.DataFrame,
    requested: object,
) -> object:
    """
    Find an EIA import-weight row using either its full label
    (e.g. 'TTL_01: Agriculture and hunting') or its identifier
    (e.g. 'TTL_01').

    Full labels are matched first. Identifier-only matching is
    used when the identifier uniquely identifies a row.
    """
    requested_text = str(requested).strip()
    requested_normalised = _normalise_text(requested_text)

    requested_identifier = requested_text.split(":", maxsplit=1)[0].strip()
    requested_identifier_normalised = _normalise_text(requested_identifier)

    full_matches = []
    identifier_matches = []

    for index in eia_weights.index:
        label = str(index).strip()
        identifier = label.split(":", maxsplit=1)[0].strip()

        if _normalise_text(label) == requested_normalised:
            full_matches.append(index)

        if _normalise_text(identifier) == requested_identifier_normalised:
            identifier_matches.append(index)

    # Prefer an exact full-label match.
    if len(full_matches) == 1:
        return full_matches[0]

    if len(full_matches) > 1:
        raise ValueError(
            f"Multiple EIA import-weight rows match the full label "
            f"{requested_text!r}: {full_matches}"
        )

    # Fall back to identifier matching, provided it is unambiguous.
    if len(identifier_matches) == 1:
        return identifier_matches[0]

    if len(identifier_matches) > 1:
        raise ValueError(
            f"EIA identifier {requested_identifier!r} matches multiple rows. "
            f"Supply the full row label to distinguish them: "
            f"{identifier_matches}"
        )

    available_identifiers = [
        str(index).split(":", maxsplit=1)[0].strip()
        for index in eia_weights.index[:10]
    ]

    raise KeyError(
        f"No EIA import-weight row matches {requested_text!r}. "
        f"Available identifiers include: {available_identifiers}"
    )




def make_oecd_to_eia_mapping(
    io_columns: list[str],
    eia_weights: pd.DataFrame,
) -> dict[str, str]:
    """
    Map OECD IO purchaser codes to EIA workbook row identifiers.

    Examples:
        A01      -> TTL_01
        B05      -> TTL_05
        C10T12   -> TTL_10T12
        C17_18   -> TTL_17T18
        C302T309 -> TTL_302T309
        H49      -> TTL_49

    The full EIA row labels are resolved by _find_eia_row(),
    which ignores descriptive text after the identifier.
    """
    mapping = {}

    for purchaser in io_columns:
        if purchaser in ACTIVITIES:
            continue

        if purchaser in IO_ELECTRICITY_COLUMN_TO_EIA:
            continue

        # OECD IO industry codes use letters followed by digits.
        # EIA identifiers use the same numeric portion, prefixed by TTL_.
        if not purchaser or not purchaser[0].isalpha():
            continue

        suffix = purchaser[1:].replace("_", "")

        if not suffix:
            continue

        candidate = f"TTL_{suffix}"

        try:
            mapping[purchaser] = _find_eia_row(
                eia_weights,
                candidate,
            )
        except KeyError:
            continue

    return mapping


def _vmfb_column_shares(
    vmfb_shares: pd.DataFrame,
    purchaser: str,
) -> pd.Series:
    """Return normalised VMFB technology shares for one IO purchaser."""

    vmfb_purchaser = ACTIVITY_TO_VMFB_PURCHASER.get(
        purchaser,
        purchaser,
    )

    if vmfb_purchaser not in vmfb_shares.columns:
        raise KeyError(
            f"IO purchasing column {purchaser!r} maps to VMFB column "
            f"{vmfb_purchaser!r}, which is missing from VMFB shares."
        )

    values = (
        vmfb_shares.reindex(index=ACTIVITIES)[vmfb_purchaser]
        .astype(float)
        .fillna(0.0)
        .clip(lower=0.0)
    )

    total = float(values.sum())

    if total <= 0:
        # A zero-share column is acceptable only if the corresponding
        # aggregate import flow in the IO table is also zero.
        return pd.Series(0.0, index=ACTIVITIES, dtype=float)

    return values / total

def _economy_wide_vmfb_shares(
    vmfb_shares: pd.DataFrame,
) -> pd.Series:
    """
    Calculate economy-wide GTAP technology shares by aggregating
    the available purchaser-specific VMFB shares.

    Each purchaser column is normalized, so this function should
    ideally be applied to the underlying VMFB values instead.
    """
    values = (
        vmfb_shares.reindex(index=ACTIVITIES)
        .astype(float)
        .fillna(0.0)
        .clip(lower=0.0)
    )

    # Exclude purchaser columns with no import allocation.
    valid_columns = values.sum(axis=0) > 0
    values = values.loc[:, valid_columns]

    if values.empty:
        raise ValueError(
            "Cannot calculate economy-wide VMFB shares: "
            "no purchaser columns have positive import shares."
        )

    # Use the average of the available normalized purchaser shares.
    shares = values.mean(axis=1)
    return shares / shares.sum()

def _expand_eia_category_weights(
    category_weights: pd.Series,
    vmfb_shares: pd.Series,
    fallback_vmfb_shares: Optional[pd.Series] = None,
) -> pd.Series:
    """
    Expand five EIA category weights into the 13 GTAP activities.

    Gas, Wind, and Solar map directly to GDT, WindBL, and SolarP.
    Hydro is split between HydroBL and HydroP using VMFB proportions.
    Other is split across all remaining technologies using VMFB
    proportions conditional on the Other category.
    """
    result = pd.Series(0.0, index=ACTIVITIES, dtype=float)

    category_weights = (
        category_weights.reindex(EIA_CATEGORIES)
        .astype(float)
        .fillna(0.0)
        .clip(lower=0.0)
    )

    category_total = float(category_weights.sum())
    if category_total <= 0:
        raise ValueError("EIA category weights sum to zero.")

    category_weights /= category_total

    direct = {
        "Gas": "GDT",
        "Wind": "WindBL",
        "Solar": "SolarP",
    }

    for category, activity in direct.items():
        result.loc[activity] = category_weights.loc[category]

    for category in ("Hydro", "Other"):
        members = [
            activity
            for activity in ACTIVITIES
            if ACTIVITY_TO_EIA[activity] == category
        ]

        conditional = vmfb_shares.reindex(members).fillna(0.0)
        conditional_total = float(conditional.sum())

        if category_weights.loc[category] > 0 and conditional_total <= 0:
            if fallback_vmfb_shares is None:
                raise ValueError(
                    f"EIA assigns a positive {category} import share, but "
                    "VMFB has no positive within-category technology shares. "
                    f"Activities: {members}"
                )

            conditional = fallback_vmfb_shares.reindex(members).fillna(0.0)
            conditional_total = float(conditional.sum())

            if conditional_total <= 0:
                raise ValueError(
                    f"Neither purchaser-specific nor economy-wide VMFB shares "
                    f"provide a positive allocation for {category}. "
                    f"Activities: {members}"
                )

        if conditional_total > 0:
            result.loc[members] = (
                category_weights.loc[category]
                * conditional
                / conditional_total
            )

    result /= result.sum()
    return result


def _ttl35_row_for_activity(activity: str) -> str:
    """Return the EIA TTL_35 row associated with an electricity activity."""
    description = ACTIVITY_TO_TTL35[activity]
    return f"TTL_35: {description}"


def _resolve_eia_row_for_purchaser(
    purchaser: str,
    eia_weights: pd.DataFrame,
    purchaser_to_eia: Mapping[str, str],
) -> str:
    """
    Resolve the EIA purchasing-sector row for an IO column.

    Priority:
      1. Explicit purchaser_to_eia mapping.
      2. Disaggregated electricity purchaser-column mapping.
      3. Direct matching of the IO column identifier to the workbook.
      4. GTAP activity or DOM_/IMP_ electricity activity mapping.
    """
    if purchaser in purchaser_to_eia:
        requested = purchaser_to_eia[purchaser]
        return _find_eia_row(eia_weights, requested)

    # Disaggregated electricity purchaser columns.
    if purchaser in IO_ELECTRICITY_COLUMN_TO_EIA:
        category = IO_ELECTRICITY_COLUMN_TO_EIA[purchaser]

        requested = {
            "Gas": "TTL_35: Gas, steam and air conditioning supply",
            "Wind": "TTL_35: Wind",
            "Solar": "TTL_35: Solar PV",
            "Hydro": "TTL_35: Hydro",
            "Other": "TTL_35: Other",
        }[category]

        return _find_eia_row(eia_weights, requested)

    # Electricity purchaser columns named using GTAP activity labels.
    if purchaser in ACTIVITIES:
        return _find_eia_row(
            eia_weights,
            _ttl35_row_for_activity(purchaser),
        )

    # Also support columns named DOM_GDT, IMP_GDT, etc.
    for prefix in ("DOM_", "IMP_"):
        if purchaser.startswith(prefix):
            activity = purchaser[len(prefix):]

            if activity in ACTIVITIES:
                return _find_eia_row(
                    eia_weights,
                    _ttl35_row_for_activity(activity),
                )

    return _find_eia_row(eia_weights, purchaser)

def make_eia_import_shares(
    io_columns: list[str],
    eia_weights: pd.DataFrame,
    vmfb_shares: pd.DataFrame,
    purchaser_to_eia: Optional[Mapping[str, str]] = None,
) -> pd.DataFrame:
    """
    Build a 13-activity import share matrix using EIA weights.

    Where an ordinary IO purchaser has no corresponding EIA row,
    fall back to its direct GTAP VMFB import shares.
    """
    purchaser_to_eia = purchaser_to_eia or {}

    result = pd.DataFrame(
        0.0,
        index=ACTIVITIES,
        columns=io_columns,
        dtype=float,
    )
    
    fallback_vmfb = _economy_wide_vmfb_shares(vmfb_shares)

    for purchaser in io_columns:
        try:
            eia_row = _resolve_eia_row_for_purchaser(
                purchaser,
                eia_weights,
                purchaser_to_eia,
            )

        except KeyError:
            # Do not silently fall back for explicitly mapped purchasers.
            if purchaser in purchaser_to_eia:
                raise

            # Electricity purchasers must have a valid TTL_35 mapping.
            is_electricity_purchaser = (
                purchaser in ACTIVITIES
                or purchaser in IO_ELECTRICITY_COLUMN_TO_EIA
                or purchaser.startswith(("DOM_", "IMP_"))
            )

            if is_electricity_purchaser:
                raise

            # No EIA row is available: use the purchaser's VMFB shares.
            import warnings

            warnings.warn(
                f"No EIA import-weight row found for IO purchaser "
                f"{purchaser!r}. Using GTAP VMFB import shares instead.",
                stacklevel=2,
            )

            result[purchaser] = _vmfb_column_shares(
                vmfb_shares=vmfb_shares,
                purchaser=purchaser,
            )
            continue

        vmfb = _vmfb_column_shares(
            vmfb_shares,
            purchaser,
        )

        result[purchaser] = _expand_eia_category_weights(
            eia_weights.loc[eia_row],
            vmfb,
            fallback_vmfb_shares=fallback_vmfb,
        )

    return result

def make_gtap_import_shares(
    io_columns: list[str],
    vmfb_shares: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build the GTAP import share matrix directly from VMFB.

    Output:
      index   = GTAP activities
      columns = intermediate IO purchasing columns

    Disaggregated electricity purchaser columns (D_Gas, D_Wind, etc.)
    use the original aggregate D purchaser shares from VMFB.
    """
    result = pd.DataFrame(
        0.0,
        index=ACTIVITIES,
        columns=io_columns,
        dtype=float,
    )

    for purchaser in io_columns:
        result[purchaser] = _vmfb_column_shares(
            vmfb_shares=vmfb_shares,
            purchaser=purchaser,
        )

    totals = result.sum(axis=0)

    # A column may sum to either one (valid allocation shares)
    # or zero (no VMFB import allocation available).
    valid = (
        np.isclose(
            totals.to_numpy(),
            1.0,
            rtol=1e-9,
            atol=1e-9,
        )
        |
        np.isclose(
            totals.to_numpy(),
            0.0,
            rtol=1e-9,
            atol=1e-9,
        )
    )

    if not valid.all():
        bad = totals.index[~valid].tolist()

        raise ValueError(
            "GTAP import shares must sum to either zero or one. "
            f"Invalid purchasing columns: "
            f"{totals.loc[bad].to_dict()}"
        )

    return result


def split_import_electricity_row(
    io: pd.DataFrame,
    import_shares: pd.DataFrame,
    source_row: str = "IMP_D",
    total_column: str = "TOTAL",
    tolerance: float = 1e-8,
) -> pd.DataFrame:
    """
    Disaggregate intermediate-industry imports from IMP_D into
    IMP_<activity> rows.

    Final-demand and explicitly non-allocated columns remain in IMP_D.

    The import share matrix must contain only the columns to be
    technology-disaggregated.
    """
    if source_row not in io.index:
        raise KeyError(
            f"Source import row {source_row!r} is not in the IO table."
        )

    transaction_columns = [
        column for column in io.columns
        if column != total_column
    ]

    allocation_columns = list(import_shares.columns)

    missing = [
        column for column in allocation_columns
        if column not in transaction_columns
    ]

    if missing:
        raise KeyError(
            "Import share matrix contains columns not present in the "
            f"IO table: {missing}"
        )

    excluded_columns = [
        column for column in transaction_columns
        if column not in allocation_columns
    ]

    shares = import_shares.reindex(
        index=ACTIVITIES,
        columns=allocation_columns,
    ).astype(float).fillna(0.0)

    source_values = io.loc[
        source_row,
        transaction_columns,
    ].astype(float)

    # Validate allocations only for columns being disaggregated.
    for column in allocation_columns:
        source_value = float(source_values[column])

        if abs(source_value) <= tolerance:
            continue

        share_total = float(shares[column].sum())

        if not np.isclose(
            share_total,
            1.0,
            atol=1e-6,
            rtol=1e-9,
        ):
            raise ValueError(
                f"Import shares for {column!r} sum to {share_total}, "
                "not one."
            )

    # Create technology-specific imported electricity rows.
    new_rows = pd.DataFrame(
        0.0,
        index=[f"IMP_{activity}" for activity in ACTIVITIES],
        columns=io.columns,
    )

    for activity in ACTIVITIES:
        new_row = f"IMP_{activity}"

        new_rows.loc[new_row, allocation_columns] = (
            source_values[allocation_columns]
            * shares.loc[activity, allocation_columns]
        )

        if total_column in io.columns:
            new_rows.loc[new_row, total_column] = (
                new_rows.loc[new_row, allocation_columns].sum()
            )

    # Retain the original IMP_D row as the residual:
    # final demand, "..", and any other excluded columns.
    residual_row = io.loc[[source_row]].copy()
    residual_row.loc[:, allocation_columns] = 0.0

    if total_column in io.columns:
        # Preserve the original aggregate TOTAL, including any rounding
        # residual relative to the displayed transaction columns.
        residual_row.loc[source_row, total_column] = (
            io.loc[source_row, total_column]
            - new_rows[total_column].sum()
        )

    # Replace IMP_D in place with the technology rows and residual row.
    before = io.loc[:source_row].drop(index=source_row)
    after = io.loc[source_row:].drop(index=source_row)

    out = pd.concat(
        [before, new_rows, residual_row, after],
        axis=0,
    ).reindex(columns=io.columns)

    # Check conservation for every transaction column.
    for column in transaction_columns:
        old_value = float(io.loc[source_row, column])

        new_value = float(
            out.loc[
                [f"IMP_{activity}" for activity in ACTIVITIES]
                + [source_row],
                column,
            ].sum()
        )

        if not np.isclose(
            old_value,
            new_value,
            atol=tolerance,
            rtol=1e-9,
        ):
            raise AssertionError(
                f"Import row conservation failed in column {column!r}: "
                f"original={old_value}, disaggregated={new_value}"
            )

    # Check conservation of the original IMP_D total.
    if total_column in io.columns:
        old_total = float(io.loc[source_row, total_column])

        new_total = float(
            out.loc[
                [f"IMP_{activity}" for activity in ACTIVITIES]
                + [source_row],
                total_column,
            ].sum()
        )

        if not np.isclose(
            old_total,
            new_total,
            atol=tolerance,
            rtol=1e-9,
        ):
            raise AssertionError(
                "Import row total is not conserved: "
                f"original={old_total}, disaggregated={new_total}"
            )

    return out


def create_import_alternatives(
    io: pd.DataFrame,
    eia_weights: Optional[pd.DataFrame],
    vmfb_shares: pd.DataFrame,
    purchaser_to_eia: Optional[Mapping[str, str]] = None,
    source_row: str = "IMP_D",
    total_column: str = "TOTAL",
) -> tuple[Optional[pd.DataFrame], pd.DataFrame]:
    """
    Return (EIA-based IO table, GTAP-based IO table).

    Only intermediate-industry purchasing columns are disaggregated.
    Final-demand and non-allocated columns remain in IMP_D.

    If eia_weights is None, the EIA alternative is skipped.
    Both alternatives, when generated, use the original IO table.
    """
    purchaser_to_eia = purchaser_to_eia or {}

    if source_row not in io.index:
        raise KeyError(
            f"Source import row {source_row!r} is not in the IO table."
        )

    transaction_columns = [
        column for column in io.columns
        if column != total_column
    ]

    # Only intermediate-industry columns are technology-disaggregated.
    allocation_columns = [
        column for column in transaction_columns
        if column not in NON_ALLOCATED_COLUMNS
    ]

    # GTAP/VMFB alternative.
    gtap_shares = make_gtap_import_shares(
        io_columns=allocation_columns,
        vmfb_shares=vmfb_shares,
    )

    io_gtap = split_import_electricity_row(
        io=io,
        import_shares=gtap_shares,
        source_row=source_row,
        total_column=total_column,
    )

    # EIA alternative, when EIA weights are available.
    io_eia = None

    if eia_weights is not None:
        eia_mapping = make_oecd_to_eia_mapping(
            io_columns=allocation_columns,
            eia_weights=eia_weights,
        )

        eia_mapping.update(purchaser_to_eia)

        eia_shares = make_eia_import_shares(
            io_columns=allocation_columns,
            eia_weights=eia_weights,
            vmfb_shares=vmfb_shares,
            purchaser_to_eia=eia_mapping,
        )

        io_eia = split_import_electricity_row(
            io=io,
            import_shares=eia_shares,
            source_row=source_row,
            total_column=total_column,
        )

    return io_eia, io_gtap


def load_country_eia_workbook(
    root: str | Path,
    country: str,
    filename: Optional[str] = None,
    sheet_name: Optional[str] = None,
) -> pd.DataFrame:
    """
    Locate and load a country's import-disaggregation workbook.
    """
    root = Path(root)

    filename = filename or f"{country} - import disaggregation .xlsx"

    country_folder = root / country

    if not country_folder.exists():
        raise FileNotFoundError(
            f"Country folder does not exist: {country_folder}"
        )

    matches = list(country_folder.rglob(filename))

    if not matches:
        matches = [
            path
            for path in country_folder.rglob("*.xlsx")
            if path.name.lower() == filename.lower()
        ]

    if len(matches) == 0:
        raise FileNotFoundError(
            f"Could not find {filename!r} under {country_folder}"
        )

    if len(matches) > 1:
        raise RuntimeError(
            f"Found more than one {filename!r}: "
            f"{[str(path) for path in matches]}"
        )

    # Use the country-specific sheet name where supplied.
    if sheet_name is None:
        sheet_name = (
            "column weights"
            if country == "Lithuania"
            else "Imports disag weights"
        )

    return load_eia_import_weights(
        matches[0],
        sheet_name=sheet_name,
    )

def save_import_alternatives(
    io_eia: Optional[pd.DataFrame],
    io_gtap: pd.DataFrame,
    output_folder: str | Path,
    country_code: str,
    year: int,
) -> dict[str, Path]:
    """Save the available import-disaggregated IO alternatives as CSV files."""
    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)

    paths = {}

    if io_eia is not None:
        eia_path = output_folder / (
            f"{country_code}{year}_disaggregated_EIA.csv"
        )
        io_eia.to_csv(eia_path)
        paths["EIA"] = eia_path

    gtap_path = output_folder / (
        f"{country_code}{year}_disaggregated_GTAP.csv"
    )
    io_gtap.to_csv(gtap_path)
    paths["GTAP"] = gtap_path

    return paths
