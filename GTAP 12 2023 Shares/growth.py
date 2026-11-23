"""
2030 BAU cumulative growth factors (2022 = 1), Table C1 of the paper
"Renewable energy expansion in the Baltic States".

Sources named in the paper: MarketLine (2026) industry forecasts; IMF (2025)
real GDP growth for public services; national electricity-demand projections
(Estonia: Energy sector development plan; Latvia: KPMG Baltics 2024;
Lithuania: Litgrid 2024) for the electricity sectors.

Every GTAP-Power generating type (the 12 generating activities in
shares_gams.GENERATING_TECHS) takes the Table C1 *electricity* factor, i.e.
the Wind / Solar PV / Hydro / Other estimate is copied to every technology
mapped to that group. GDT (gas, steam and air conditioning supply) takes the
"Gas, steam and air conditioning supply" factor.
"""
from __future__ import annotations

# Order of every tuple: (Estonia EST, Latvia LVA, Lithuania LTU)
COUNTRIES = ("EST", "LVA", "LTU")

# --------------------------------------------------------------------------
# Table C1, keyed by OECD sector code (codes as used in mapping.py).
# --------------------------------------------------------------------------
_AGRI = (1.253, 0.959, 1.101)       # MarketLine: agriculture, hunting, forestry, fishing
_MINING = (0.893, 1.104, 1.126)     # MarketLine: mining, manufacturing and utilities
_MANUF = (0.979, 1.060, 1.213)      # MarketLine: manufacturing
_TRANSPORT = (0.996, 1.033, 1.276)  # MarketLine: transport, storage and communications
_WHOLESALE = (1.209, 1.178, 1.111)  # MarketLine: wholesale, retail trade, restaurants, hotels
_SERVICES = (1.113, 1.130, 1.200)   # MarketLine: services
_PUBLIC = (1.064, 1.163, 1.198)     # IMF: real GDP growth
_OTHER_SERV = (1.133, 1.145, 1.215)  # MarketLine: other services

TABLE_C1 = {
    "A01": _AGRI, "A02": _AGRI, "A03": _AGRI,
    "B05": _MINING, "B06": _MINING, "B07": _MINING, "B08": _MINING, "B09": _MINING,
    "C10T12": _MANUF, "C13T15": _MANUF, "C16": _MANUF, "C17_18": _MANUF,
    "C19": _MANUF, "C20": _MANUF, "C21": _MANUF, "C22": _MANUF, "C23": _MANUF,
    "C24A": _MANUF, "C24B": _MANUF, "C25": _MANUF, "C26": _MANUF, "C27": _MANUF,
    "C28": _MANUF, "C29": _MANUF, "C301": _MANUF, "C302T309": _MANUF,
    "C31T33": _MANUF,
    "D_GAS": (0.979, 1.104, 1.126),      # Gas, steam and air conditioning supply
    "D_ELEC": (1.460, 1.427, 1.553),     # Wind = Solar PV = Hydro = Other
    "E": _MINING,                        # Water supply, sewerage, waste
    "F": (0.895, 1.200, 1.416),          # Construction
    "G": _WHOLESALE,
    "H49": _TRANSPORT, "H50": _TRANSPORT, "H51": _TRANSPORT,
    "H52": _TRANSPORT, "H53": _TRANSPORT,
    "I": _WHOLESALE,                     # Accommodation and food service
    "J58T60": _TRANSPORT,                # Publishing, audiovisual, broadcasting
    "J61": _TRANSPORT,                   # Telecommunications
    "J62_63": _SERVICES,                 # IT and other information services
    "K": _SERVICES, "L": _SERVICES, "M": _SERVICES, "N": _SERVICES,
    "O": _PUBLIC, "P": _PUBLIC, "Q": _PUBLIC, "R": _PUBLIC,
    "S": _OTHER_SERV,
    "T": _PUBLIC,                        # Households as employers
}

# Alternative spellings of the same OECD sector that may appear in table labels.
ALIASES = {
    "A01_02": "A01", "A01_03": "A01",
    "B05_06": "B05", "B07_08": "B07",
    "D": "D_ELEC", "D35": "D_ELEC",
    "D_Wind": "D_ELEC", "D_Solar": "D_ELEC", "D_Hydro": "D_ELEC", "D_Other": "D_ELEC",
    "D_Gas": "D_GAS",
}

# Generic fallbacks by first letter, used only if the exact code is not listed.
_PREFIX_FALLBACK = {"A": "A01", "B": "B05", "C": "C10T12", "H": "H49"}

# GTAP-Power activity -> Table C1 row (all 12 generating types use the electricity row).
ELECTRICITY_ROW = "D_ELEC"
GAS_ROW = "D_GAS"


def growth_factor(code: str, country_code: str, activities=None) -> float:
    """
    Cumulative 2022 -> 2030 growth factor for a sector label.

    `code` may be an OECD sector code, a GTAP-Power activity (e.g. "WindBL",
    "SolarP", "GDT"), or an electricity label such as "D".
    """
    if country_code not in COUNTRIES:
        raise KeyError(f"Unknown country code {country_code!r}; expected one of {COUNTRIES}.")
    idx = COUNTRIES.index(country_code)

    if activities is None:
        from shares_gams import ACTIVITIES as activities  # local import: needs gams
    if code in activities:
        key = GAS_ROW if code == "GDT" else ELECTRICITY_ROW
    else:
        key = ALIASES.get(code, code)
        if key not in TABLE_C1:
            fallback = _PREFIX_FALLBACK.get(key[:1])
            if fallback is None:
                raise KeyError(
                    f"No Table C1 growth factor for sector label {code!r}. "
                    "Add it to growth.TABLE_C1 or growth.ALIASES."
                )
            key = fallback
    return TABLE_C1[key][idx]
