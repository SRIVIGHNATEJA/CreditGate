import os
import re
import hashlib
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from decimal import Decimal

# MCA Standard Regex for 21-character Corporate Identification Number
# Format: [Listing: U/L][NIC: 5 digits][State: 2 chars][Year: 4 digits][Entity: 3 chars][RegNum: 6 digits]
CIN_REGEX = re.compile(r"^([UL])(\d{5})([A-Z]{2})(\d{4})([A-Z]{3})(\d{6})$")

# Indian PAN Regex: 5 letters, 4 digits, 1 letter
PAN_REGEX = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

# 4th character of PAN indicates legal entity structure under Indian Income Tax Act
PAN_ENTITY_MAP = {
    "C": "Company (Public/Private Limited)",
    "P": "Individual / Proprietorship",
    "F": "Partnership Firm / LLP",
    "H": "Hindu Undivided Family (HUF)",
    "A": "Association of Persons (AOP)",
    "T": "Trust",
    "B": "Body of Individuals (BOI)",
    "L": "Local Authority",
    "J": "Artificial Juridical Person",
    "G": "Government Agency"
}

# MCA Entity Codes mapped to legal structures
MCA_ENTITY_MAP = {
    "PTC": "Private Limited Company",
    "PLC": "Public Limited Company",
    "FTC": "Subsidiary of Foreign Company",
    "ULL": "Public Limited Company (Unlimited Liability)",
    "ULT": "Private Limited Company (Unlimited Liability)",
    "GAP": "Government Association / Body",
    "GOI": "Union Government Company",
    "SGC": "State Government Company",
    "NPL": "Non-Profit License Company (Section 8)"
}

# Major NIC 2-digit and 5-digit industry sector taxonomy (National Industrial Classification 2008)
NIC_SECTOR_TAXONOMY = {
    "29253": "Manufacture of machine tools & precision industrial tooling",
    "29": "Manufacture of motor vehicles, trailers & precision machinery",
    "60200": "Freight transport by road & logistics management",
    "60": "Land transport & transport via pipelines",
    "24": "Manufacture of basic metals (Steel, Iron, Alloys)",
    "24101": "Manufacture of steel, billets, structural infrastructure",
    "62": "Computer programming, consultancy & IT information services",
    "10": "Manufacture of food products & agro-processing",
    "13": "Manufacture of textiles & synthetic fabrics",
    "21": "Manufacture of pharmaceuticals, medicinal chemicals",
    "45": "Wholesale and retail trade of automotive vehicles & spares",
    "46": "Wholesale trade (except of motor vehicles and motorcycles)",
    "47": "Retail trade (B2C & commercial distribution)",
    "41": "Construction of buildings & infrastructure projects",
    "43": "Specialized construction activities",
    "70": "Activities of head offices; management consultancy",
}

STATE_CODES_MAP = {
    "DL": "Delhi", "KA": "Karnataka", "MH": "Maharashtra", "TN": "Tamil Nadu",
    "GJ": "Gujarat", "TS": "Telangana", "HR": "Haryana", "UP": "Uttar Pradesh",
    "WB": "West Bengal", "RJ": "Rajasthan", "MP": "Madhya Pradesh", "KL": "Kerala",
    "AP": "Andhra Pradesh", "PB": "Punjab", "OR": "Odisha", "CH": "Chandigarh"
}


def validate_cin_syntax(cin_str: str) -> Tuple[bool, Optional[str]]:
    """Validates whether a CIN strictly adheres to the 21-character MCA specification."""
    if not cin_str:
        return False, "CIN cannot be empty"
    clean_cin = cin_str.strip().upper()
    if len(clean_cin) != 21:
        return False, f"Invalid length ({len(clean_cin)} chars). MCA CIN must be exactly 21 characters."
    if not CIN_REGEX.match(clean_cin):
        return False, "Invalid CIN format. Expected pattern: U/L + 5-digit NIC + 2-letter State + 4-digit Year + 3-letter Entity + 6-digit RegNo"
    return True, None


def parse_cin(cin_str: str, current_year: int = 2026) -> Dict[str, Any]:
    """
    Algorithmic parsing of a 21-character CIN.
    Zero API cost, zero external dependencies, 0.0001s deterministic execution.
    """
    is_valid, err = validate_cin_syntax(cin_str)
    if not is_valid:
        raise ValueError(f"CIN validation failed: {err}")

    clean = cin_str.strip().upper()
    match = CIN_REGEX.match(clean)
    assert match is not None

    listing_char, nic_code, state_code, year_str, entity_code, reg_num = match.groups()

    listing_status = "Listed" if listing_char == "L" else "Unlisted"
    incorporation_year = int(year_str)
    vintage_years = max(0, current_year - incorporation_year)

    # NIC Sector lookup: try exact 5-digit code first, then fall back to 2-digit macro sector
    sector_name = NIC_SECTOR_TAXONOMY.get(
        nic_code,
        NIC_SECTOR_TAXONOMY.get(nic_code[:2], f"Specialized Industrial Sector (NIC {nic_code[:2]})")
    )

    state_name = STATE_CODES_MAP.get(state_code, f"State Code: {state_code}")
    entity_desc = MCA_ENTITY_MAP.get(entity_code, f"Entity Code: {entity_code}")

    # Risk policy rule: young businesses (< 3 years vintage) carry statistical default risk
    low_vintage_risk = vintage_years < 3

    return {
        "cin": clean,
        "is_valid": True,
        "listing_status": listing_status,
        "nic_code": nic_code,
        "macro_sector": sector_name,
        "state_code": state_code,
        "state_name": state_name,
        "incorporation_year": incorporation_year,
        "business_vintage_years": vintage_years,
        "low_vintage_risk_flag": low_vintage_risk,
        "entity_code": entity_code,
        "entity_description": entity_desc,
        "registration_number": reg_num
    }


def validate_pan_syntax(pan_str: str) -> Tuple[bool, Optional[str]]:
    """Validates whether a PAN string adheres to the Indian Income Tax 10-character standard."""
    if not pan_str:
        return False, "PAN cannot be empty"
    clean_pan = pan_str.strip().upper()
    if len(clean_pan) != 10:
        return False, f"Invalid length ({len(clean_pan)} chars). Indian PAN must be exactly 10 characters."
    if not PAN_REGEX.match(clean_pan):
        return False, "Invalid PAN format. Must be 5 letters + 4 digits + 1 letter (e.g. AAACA1234B)."
    return True, None


def check_entity_consistency(
    pan_str: str,
    is_corporate: bool = True,
    cin_entity_code: Optional[str] = None
) -> Dict[str, Any]:
    """
    Cross-checks the 4th character of PAN against corporate onboarding parameters.
    Catches fraud/error where an applicant applies for a corporate loan using a personal PAN.
    """
    is_valid, err = validate_pan_syntax(pan_str)
    if not is_valid:
        return {
            "pan": pan_str,
            "is_valid": False,
            "error": err,
            "consistency_status": "SYNTAX_ERROR",
            "mismatch_detected": False,
            "pan_entity_char": None,
            "pan_entity_type": "Invalid Syntax",
            "warning_message": err,
        }

    clean_pan = pan_str.strip().upper()
    entity_char = clean_pan[3]
    pan_entity_type = PAN_ENTITY_MAP.get(entity_char, "Unknown Entity Structure")

    mismatch_detected = False
    warning_message = None

    if is_corporate:
        # Corporate entities (Private/Public Ltd) must have 'C' as 4th char of PAN
        if entity_char != "C":
            mismatch_detected = True
            warning_message = (
                f"Entity Mismatch: Applicant declared Corporate entity ({cin_entity_code or 'Company'}), "
                f"but PAN 4th character '{entity_char}' denotes '{pan_entity_type}'."
            )
    else:
        # Sole proprietorships use individual PAN ('P') or partnership ('F')
        if entity_char == "C":
            mismatch_detected = True
            warning_message = (
                f"Entity Note: Non-corporate toggle selected, but PAN indicates Registered Company ('C')."
            )

    return {
        "pan": clean_pan,
        "is_valid": True,
        "pan_entity_char": entity_char,
        "pan_entity_type": pan_entity_type,
        "mismatch_detected": mismatch_detected,
        "warning_message": warning_message,
        "consistency_status": "FLAG_MISMATCH" if mismatch_detected else "CONSISTENT"
    }


def generate_application_id(
    company_name: str,
    date_stamp: Optional[str] = None,
    session_suffix: Optional[str] = None
) -> str:
    """
    Generates a unique CRM-style tracking key: APP-[YYYYMMDD]-[HASH]-[SUFFIX].
    Binds the entire underwriting trace, HITL edits, and CAM export to a single identifier.
    Includes session/timestamp entropy so multiple sessions on the same day do not collide.
    """
    if not date_stamp:
        date_stamp = datetime.now().strftime("%Y%m%d")
    clean_name = re.sub(r"[^A-Za-z0-9]", "", company_name.upper())
    hash_digest = hashlib.sha256(f"{clean_name}_{date_stamp}".encode("utf-8")).hexdigest()[:6].upper()
    if session_suffix is None:
        session_suffix = f"{datetime.now().strftime('%H%M%S')}{os.urandom(2).hex().upper()}"
    if session_suffix:
        return f"APP-{date_stamp}-{hash_digest}-{session_suffix}"
    return f"APP-{date_stamp}-{hash_digest}"
