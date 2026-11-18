# Mapping of GTAP commodities to OECD input-output sectors (Table A3,
# Chepeliev (2023); OECD (2025)). Keys are GTAP COMM codes as stored in the
# GDX database; values are the OECD IO sector codes used in the country
# "ttl"/"dom" tables. Where a single GTAP sector spans several OECD sectors
# the combined OECD group is used:
#   B07T09   = B07 + B08 + B09 (no separate GTAP mining-support sector)
#   C302T309 includes C301 (no separate GTAP shipbuilding sector)
#   H53_J    = H53 + J58T60 + J61 + J62_63 (postal combined with communication)
#   M_N_S    = M + N + S (GTAP business services nec)
# The electricity sector D is kept split by generation technology, matching
# the D35 disaggregation used in the country IO workbooks (Wind, Solar,
# Hydro, Other, and Gas, steam and ac supply).
# DWE maps to None: no corresponding OECD sector, excluded (usually embedded
# in imputed consumption of housing services, part of value added).
GTAP_TO_OECD = {
    # A01: Agriculture and hunting
    "PDR": "A01", "WHT": "A01", "GRO": "A01", "V_F": "A01", "OSD": "A01",
    "C_B": "A01", "PFB": "A01", "OCR": "A01", "CTL": "A01", "OAP": "A01",
    "RMK": "A01", "WOL": "A01",
    "FRS": "A02",          # Forestry and logging
    "FSH": "A03",          # Fishing and aquaculture
    "COA": "B05",          # Mining of coal and lignite
    "OIL": "B06",          # Extraction of crude petroleum and natural gas
    "GAS": "B06",
    # C10T12: Food products, beverages and tobacco
    "CMT": "C10T12", "OMT": "C10T12", "VOL": "C10T12", "MIL": "C10T12",
    "PCR": "C10T12", "SGR": "C10T12", "OFD": "C10T12", "B_T": "C10T12",
    # C13T15: Textiles, wearing apparel, leather
    "TEX": "C13T15", "WAP": "C13T15", "LEA": "C13T15",
    "LUM": "C16",          # Wood and cork
    "PPP": "C17_18",       # Paper products and printing
    "P_C": "C19",          # Coke and refined petroleum products
    "CHM": "C20",          # Chemicals and chemical products
    "BPH": "C21",          # Basic pharmaceutical products
    "RPP": "C22",          # Rubber and plastic products
    "NMM": "C23",          # Other non-metallic mineral products
    "I_S": "C24A",         # Basic iron and steel
    "NFM": "C24B",         # Basic precious and other non-ferrous metals
    "FMP": "C25",          # Fabricated metal products
    "ELE": "C26",          # Computer, electronic and optical equipment
    "EEQ": "C27",          # Electrical equipment
    "OME": "C28",          # Machinery and equipment nec
    "MVH": "C29",          # Motor vehicles, trailers and semi-trailers
    "OTN": ["C301", "C302T309"],     # Other transport equipment (incl. C301 shipbuilding)
    "OMF": "C31T33",       # Manufacturing nec; repair and installation
    # D: Electricity, gas, steam and air conditioning supply, by technology
    "WindBL": "D_Wind",
    "SolarP": "D_Solar",
    "HydroBL": "D_Hydro", "HydroP": "D_Hydro",
    "TnD": "D_Other", "NuclearBL": "D_Other", "CoalBL": "D_Other",
    "GasBL": "D_Other", "OilBL": "D_Other", "OtherBL": "D_Other",
    "GasP": "D_Other", "OilP": "D_Other",
    "GDT": "D_Gas",        # Gas manufacture and distribution, steam and A/C supply
    "WTR": "E",            # Water supply; sewerage, waste management
    "CNS": "F",            # Construction
    "TRD": "G",            # Wholesale and retail trade; repair of motor vehicles
    "OTP": "H49",          # Land transport
    "WTP": "H50",          # Water transport
    "ATP": "H51",          # Air transport
    "WHS": "H52",          # Warehousing and support activities
    "AFS": "I",            # Accommodation and food service activities
    "OFI": "K",            # Financial services nec
    "INS": "K",            # Insurance
    "RSA": "L",            # Real estate activities
    "OXT": ["B07", "B08", "B09"],   # Mining activities
    # Output weights.
    
    "CMN": ["H53", "J58T60", "J61", "J62_63"],  # Transportation and Communications
    "OBS": ["M", "N", "S"],  # Business services nec
    "ROS": ["R", "T"],       # Arts, entertainment and recreation
    "OSG": "O",            # Public administration and defence
    "EDU": "P",            # Education
    "HHT": "Q",            # Human health and social work activities
    "DWE": None,           # Dwellings: excluded, no corresponding OECD sector
}

# Full names of the OECD IO sectors (and of the GTAP factor inputs), used to
# label the rows of the OECD-based input shares output. Titles match the
# sector titles used in the country OECD IO workbooks; combined groups join
# the titles of their member sectors with ";".
OECD_NAMES = {
    "A01": "Agriculture and hunting",
    "A02": "Forestry and logging",
    "A03": "Fishing and aquaculture",
    "B05": "Mining of coal and lignite",
    "B06": "Extraction of crude petroleum and natural gas",
    "B07T09": "Mining of metal ores; Other mining and quarrying; Mining support service activities",
    "C10T12": "Manufacture of food products; beverages and tobacco products",
    "C13T15": "Manufacture of textiles, wearing apparel, leather and related products",
    "C16": "Manufacture of wood and of products of wood and cork",
    "C17_18": "Manufacture of paper and paper products; Printing and reproduction of recorded media",
    "C19": "Manufacture of coke and refined petroleum products",
    "C20": "Manufacture of chemicals and chemical products",
    "C21": "Manufacture of basic pharmaceutical products and pharmaceutical preparations",
    "C22": "Manufacture of rubber and plastic products",
    "C23": "Manufacture of other non-metallic mineral products",
    "C24A": "Manufacture of basic iron and steel",
    "C24B": "Manufacture of basic precious and other non-ferrous metals",
    "C25": "Fabricated metal products",
    "C26": "Computer, electronic and optical equipment",
    "C27": "Electrical equipment",
    "C28": "Machinery and equipment, nec",
    "C301": "Building of ships and boats",
    "C29": "Motor vehicles, trailers and semi-trailers",
    "C302T309": "Manufacture of other transport equipment (incl. building of ships and boats)",
    "C31T33": "Manufacturing nec; repair and installation of machinery and equipment",
    "D_Wind": "Wind",
    "D_Solar": "Solar",
    "D_Hydro": "Hydro",
    "D_Other": "Other",
    "D_Gas": "Gas, steam and ac supply",
    "E": "Water supply; sewerage, waste management and remediation activities",
    "F": "Construction",
    "G": "Wholesale and retail trade; repair of motor vehicles",
    "H49": "Land transport and transport via pipelines",
    "H50": "Water transport",
    "H51": "Air transport",
    "H52": "Warehousing and support activities for transportation",
    "H53_J": "Postal and courier activities; Publishing, audiovisual and broadcasting activities; Telecommunications; IT and other information services",
    "I": "Accommodation and food service activities",
    "K": "Financial and insurance activities",
    "L": "Real estate activities",
    "M_N_S": "Professional, scientific and technical activities; Administrative and support services; Other service activities",
    "O": "Public administration and defence; compulsory social security",
    "P": "Education",
    "Q": "Human health and social work activities",
    "R": "Arts, entertainment and recreation",
    # GTAP factor (ENDW) inputs
    "tech_aspros": "Technicians and associate professionals",
    "clerks": "Clerks",
    "service_shop": "Service and shop workers",
    "off_mgr_pros": "Officials, managers and professionals",
    "ag_othlowsk": "Agricultural and other low-skilled workers",
    "Capital": "Capital",
    "NatlRes": "Natural resources",
    "Land": "Land",
}
