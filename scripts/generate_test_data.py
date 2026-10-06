import os
import sys
import json
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from pypdf import PdfReader

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "data" / "synthetic"
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


def format_inr(d: Decimal) -> str:
    """Formats Decimal into standard Indian comma grouping (e.g., 12,50,00,000.00)."""
    s = f"{d:.2f}"
    parts = s.split(".")
    integer_part = parts[0]
    decimal_part = parts[1]

    is_negative = False
    if integer_part.startswith("-"):
        is_negative = True
        integer_part = integer_part[1:]

    if len(integer_part) <= 3:
        formatted = integer_part
    else:
        last3 = integer_part[-3:]
        rest = integer_part[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        formatted = ",".join(groups) + "," + last3

    if is_negative:
        formatted = "-" + formatted
    return f"{formatted}.{decimal_part}"


COHORTS = {
    "cohort_1_prime": {
        "company_name": "Apex Synthetic Holdings (Demo)",
        "cin": "U29253DL2018PTC334120",
        "pan": "AAACA1234B",
        "gstin": "07AAACA1234B1Z5",
        "bank_meta": {
            "bank_name": "HDFC Bank Ltd",
            "branch_name": "Industrial Finance Branch, Okhla",
            "ifsc_code": "HDFC0000128",
            "statement_period": "01-Apr-2025 to 31-Mar-2026",
            "account_number": "50200019283741",
            "address": "Plot 42, Okhla Industrial Area Ph-III, New Delhi 110020",
            "opening_balance": Decimal("4520500.00"),
            "total_credits": Decimal("125000000.00"),
            "total_debits": Decimal("118000000.00"),
            "closing_balance": Decimal("11520500.00"),
            "bounce_count": 0,
        },
        "transactions": [
            # Inward remittances (Credits = 12.50 Cr)
            {"date": "07-Apr-2025", "narration": "NEFT/INW/BOSCH_INDIA_LTD/INV_09182/PO_2025_001", "ref_no": "N09482103", "debit": None, "credit": Decimal("14500000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "15-May-2025", "narration": "RTGS/INW/TATA_ADVANCED_SYS/MILESTONE1_DEFENCE", "ref_no": "R00291823", "debit": None, "credit": Decimal("18000000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "28-Jun-2025", "narration": "ACH/DEBIT/BAJAJ_FIN_LOAN_EMI/Q1_INSTALLMENT", "ref_no": "ACH881920", "debit": Decimal("1000000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": True, "is_tax": False},
            {"date": "14-Jul-2025", "narration": "CMS/COLL/MAHINDRA_AUTO/SUPPLY_P88_CLEARANCE", "ref_no": "CMS992104", "debit": None, "credit": Decimal("12500000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "04-Aug-2025", "narration": "RTGS/OUT/TATA_STEEL_LTD/RAW_MATERIAL_ALLOY", "ref_no": "R99102834", "debit": Decimal("21590000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "22-Aug-2025", "narration": "NEFT/INW/BHARAT_FORGE_LTD/PO_99182_SETTLEMENT", "ref_no": "N11029381", "debit": None, "credit": Decimal("16000000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "18-Sep-2025", "narration": "ACH/DEBIT/BAJAJ_FIN_LOAN_EMI/Q2_INSTALLMENT", "ref_no": "ACH992811", "debit": Decimal("1000000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": True, "is_tax": False},
            {"date": "20-Sep-2025", "narration": "TAX/GST_PAYMENT_CHLN_09210/H1_STATUTORY_DISCHARGE", "ref_no": "CHL882190", "debit": Decimal("11205000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": True},
            {"date": "12-Oct-2025", "narration": "NEFT/OUT/DMG_MORI_CNC/EQUIP_LEASE_RENTAL", "ref_no": "N44918290", "debit": Decimal("15000000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "05-Nov-2025", "narration": "RTGS/INW/L_AND_T_DEFENCE/ADV_PAYMENT_RADAR_HOUSING", "ref_no": "R77291048", "debit": None, "credit": Decimal("21000000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "25-Nov-2025", "narration": "NEFT/OUT/BSES_RAJDHANI_POWER/INDUSTRIAL_HIGH_TENSION", "ref_no": "N88201948", "debit": Decimal("12000000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "15-Dec-2025", "narration": "ACH/DEBIT/BAJAJ_FIN_LOAN_EMI/Q3_INSTALLMENT", "ref_no": "ACH102938", "debit": Decimal("1000000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": True, "is_tax": False},
            {"date": "22-Dec-2025", "narration": "CMS/COLL/MARUTI_SUZUKI_INDIA/DISPATCH_SERIES_33", "ref_no": "CMS881920", "debit": None, "credit": Decimal("13500000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "10-Jan-2026", "narration": "SALARY/CORP_SALARY_DISBURSEMENT_H1_BATCH", "ref_no": "SAL992109", "debit": Decimal("21000000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "28-Jan-2026", "narration": "NEFT/INW/SUNDRAM_FASTENERS/BATCH_44_MACHINING", "ref_no": "N66291028", "debit": None, "credit": Decimal("14500000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "14-Feb-2026", "narration": "RTGS/INW/HYUNDAI_MOTOR_INDIA/ANNUAL_TOOLING_SETTLE", "ref_no": "R44910283", "debit": None, "credit": Decimal("15000000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "10-Mar-2026", "narration": "ACH/DEBIT/BAJAJ_FIN_LOAN_EMI/Q4_INSTALLMENT", "ref_no": "ACH339182", "debit": Decimal("1000000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": True, "is_tax": False},
            {"date": "20-Mar-2026", "narration": "TAX/GST_PAYMENT_CHLN_18492/H2_STATUTORY_DISCHARGE", "ref_no": "CHL993810", "debit": Decimal("11205000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": True},
            {"date": "25-Mar-2026", "narration": "SALARY/CORP_SALARY_DISBURSEMENT_H2_BATCH", "ref_no": "SAL110293", "debit": Decimal("22000000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
        ],
        "gst": {
            "legal_name": "Apex Synthetic Holdings (Demo)",
            "trade_name": "Apex Synthetic Holdings (Demo)",
            "tax_period": "March 2026 (Annualized FY 2025-26 Summary)",
            "filing_date": "20-Apr-2026",
            "arn": "AA0703260019283",
            "taxable_value": format_inr(Decimal("124500000.00")),
            "igst_amount": format_inr(Decimal("11205000.00")),
            "cgst_amount": format_inr(Decimal("5602500.00")),
            "sgst_amount": format_inr(Decimal("5602500.00")),
            "total_tax_paid": format_inr(Decimal("22410000.00")),
        },
        "itr": {
            "assessment_year": "2026-27",
            "financial_year": "2025-26",
            "filing_date": "15-Sep-2026",
            "ack_number": "928301928471029",
            "gross_total_income": format_inr(Decimal("124500000.00")),
            "depreciation": format_inr(Decimal("3000000.00")),
            "interest_paid": format_inr(Decimal("2500000.00")),
            "profit_after_tax": format_inr(Decimal("12000000.00")),
            "net_worth": format_inr(Decimal("65000000.00")),
            "total_debt": format_inr(Decimal("18000000.00")),
            "existing_emi_obligations": format_inr(Decimal("4000000.00")),
        }
    },
    "cohort_2_distressed": {
        "company_name": "Kaveri Fictitious Freight (Demo)",
        "cin": "U60200KA2019PTC120481",
        "pan": "AAACK5678M",
        "gstin": "29AAACK5678M1Z2",
        "bank_meta": {
            "bank_name": "State Bank of India",
            "branch_name": "Commercial Branch, Peenya, Bengaluru",
            "ifsc_code": "SBIN0004120",
            "statement_period": "01-Apr-2025 to 31-Mar-2026",
            "account_number": "38291049281",
            "address": "Plot 88, 4th Phase, Peenya Industrial Area, Bengaluru 560058",
            "opening_balance": Decimal("810200.00"),
            "total_credits": Decimal("42000000.00"),
            "total_debits": Decimal("41800000.00"),
            "closing_balance": Decimal("1010200.00"),
            "bounce_count": 5,
        },
        "transactions": [
            # Inward credits: 4.20 Cr
            {"date": "08-Apr-2025", "narration": "NEFT/INW/FLIPKART_INDIA_PVT_LTD/FRT_APRIL", "ref_no": "N88192019", "debit": None, "credit": Decimal("8500000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "14-May-2025", "narration": "CHQ RTN: 482910 / INSUFFICIENT FUNDS IN A/C", "ref_no": "RTN001", "debit": None, "credit": None, "is_bounce": True, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "14-May-2025", "narration": "CHQ RTN CHARGE / INSUFFICIENT FUNDS (PENAL FEE)", "ref_no": "CHG001", "debit": Decimal("590.00"), "credit": None, "is_bounce": False, "is_charge": True, "is_emi": False, "is_tax": False},
            {"date": "29-May-2025", "narration": "RTGS/INW/RELIANCE_RETAIL_LOGISTICS_Q1", "ref_no": "R44918290", "debit": None, "credit": Decimal("9000000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "10-Jun-2025", "narration": "ACH/DEBIT/HDFC_BANK_FLEET_LOAN_EMI_Q1", "ref_no": "ACH449102", "debit": Decimal("1500000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": True, "is_tax": False},
            {"date": "22-Jul-2025", "narration": "ECS RETURN: TATA MOTORS FINANCE / INSUFFICIENT FUNDS", "ref_no": "RTN002", "debit": None, "credit": None, "is_bounce": True, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "22-Jul-2025", "narration": "ECS RETURN CHARGE / AUTO-DEBIT FAILED", "ref_no": "CHG002", "debit": Decimal("590.00"), "credit": None, "is_bounce": False, "is_charge": True, "is_emi": False, "is_tax": False},
            {"date": "15-Aug-2025", "narration": "RTGS/OUT/HPCL_DIESEL_FLEET_CARDS_BULK", "ref_no": "R88291048", "debit": Decimal("12000000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "04-Sep-2025", "narration": "CMS/COLL/DELHI_VERY_LTD/OUTWARD_CARGO", "ref_no": "CMS449102", "debit": None, "credit": Decimal("7000000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "11-Sep-2025", "narration": "CHQ RTN: 882912 / BALANCE EXCEEDED DRAWING POWER", "ref_no": "RTN003", "debit": None, "credit": None, "is_bounce": True, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "11-Sep-2025", "narration": "CHQ RTN CHARGE / FINANCIAL PENALTY DEBIT", "ref_no": "CHG003", "debit": Decimal("590.00"), "credit": None, "is_bounce": False, "is_charge": True, "is_emi": False, "is_tax": False},
            {"date": "20-Sep-2025", "narration": "TAX/GST_PAYMENT_CHLN_4819_ANNUAL_DISCHARGE", "ref_no": "CHL449102", "debit": Decimal("7380000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": True},
            {"date": "15-Oct-2025", "narration": "ACH/DEBIT/HDFC_BANK_FLEET_LOAN_EMI_Q2", "ref_no": "ACH882910", "debit": Decimal("1500000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": True, "is_tax": False},
            {"date": "05-Nov-2025", "narration": "NEFT/INW/BLUE_DART_EXPRESS/LINEHAUL_CONTRACT", "ref_no": "N33918290", "debit": None, "credit": Decimal("6500000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "19-Nov-2025", "narration": "INWARD CLG RET: DIXON LOGISTICS / PAYMENT STOPPED", "ref_no": "RTN004", "debit": None, "credit": None, "is_bounce": True, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "19-Nov-2025", "narration": "INWARD CLEARING RETURN CHARGE", "ref_no": "CHG004", "debit": Decimal("590.00"), "credit": None, "is_bounce": False, "is_charge": True, "is_emi": False, "is_tax": False},
            {"date": "10-Dec-2025", "narration": "NEFT/OUT/IHMCL_FASTAG_COMMERCIAL_TOPUP", "ref_no": "N99281048", "debit": Decimal("4500000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "18-Jan-2026", "narration": "RTGS/INW/AMAZON_TRANSPORT_SERVICES_EXP", "ref_no": "R11928374", "debit": None, "credit": Decimal("6000000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "05-Feb-2026", "narration": "NACH RET: AXIS BANK BUSINESS LOAN / REJECTED", "ref_no": "RTN005", "debit": None, "credit": None, "is_bounce": True, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "05-Feb-2026", "narration": "NACH MANDATE BOUNCE PENAL SURCHARGE", "ref_no": "CHG005", "debit": Decimal("590.00"), "credit": None, "is_bounce": False, "is_charge": True, "is_emi": False, "is_tax": False},
            {"date": "12-Feb-2026", "narration": "ACH/DEBIT/HDFC_BANK_FLEET_LOAN_EMI_Q3", "ref_no": "ACH662910", "debit": Decimal("1500000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": True, "is_tax": False},
            {"date": "24-Feb-2026", "narration": "NEFT/OUT/APOLLO_TYRES_LTD/MAINT_SPARES", "ref_no": "N88201928", "debit": Decimal("5417050.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "15-Mar-2026", "narration": "SALARY/DRIVER_WAGES_AND_TRIP_ALLOWANCES", "ref_no": "WAG449102", "debit": Decimal("8000000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "22-Mar-2026", "narration": "CMS/COLL/SCHNEIDER_ELECTRIC_FREIGHT", "ref_no": "CMS110293", "debit": None, "credit": Decimal("5000000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
        ],
        "gst": {
            "legal_name": "Kaveri Fictitious Freight (Demo)",
            "trade_name": "Kaveri Fictitious Freight (Demo)",
            "tax_period": "March 2026 (Annualized FY 2025-26 Summary)",
            "filing_date": "28-Apr-2026",
            "arn": "AA2903260049182",
            "taxable_value": format_inr(Decimal("41000000.00")),
            "igst_amount": format_inr(Decimal("4920000.00")),
            "cgst_amount": format_inr(Decimal("1230000.00")),
            "sgst_amount": format_inr(Decimal("1230000.00")),
            "total_tax_paid": format_inr(Decimal("7380000.00")),
        },
        "itr": {
            "assessment_year": "2026-27",
            "financial_year": "2025-26",
            "filing_date": "29-Oct-2026",
            "ack_number": "819204819284102",
            "gross_total_income": format_inr(Decimal("41000000.00")),
            "depreciation": format_inr(Decimal("1000000.00")),
            "interest_paid": format_inr(Decimal("3500000.00")),
            "profit_after_tax": format_inr(Decimal("12000000.00")),
            "net_worth": format_inr(Decimal("4000000.00")),
            "total_debt": format_inr(Decimal("32000000.00")),
            "existing_emi_obligations": format_inr(Decimal("4500000.00")),
        }
    },
    "cohort_3_mismatch": {
        "company_name": "Bharat Mock Steels & Infra (Demo)",
        "cin": "AAB-4921",
        "pan": "AABFB8910N",
        "gstin": "27AABFB8910N1Z8",
        "bank_meta": {
            "bank_name": "ICICI Bank Ltd",
            "branch_name": "Nariman Point Branch, Mumbai",
            "ifsc_code": "ICIC0000004",
            "statement_period": "01-Apr-2025 to 31-Mar-2026",
            "account_number": "000405018291",
            "address": "1204, Maker Chambers V, Nariman Point, Mumbai 400021",
            "opening_balance": Decimal("1580000.00"),
            "total_credits": Decimal("88000000.00"),
            "total_debits": Decimal("85000000.00"),
            "closing_balance": Decimal("4580000.00"),
            "bounce_count": 1,
        },
        "transactions": [
            # Inward credits: 8.80 Cr (vs 4.50 Cr GST declared!)
            {"date": "15-Apr-2025", "narration": "NEFT/INW/JSW_STEEL_DISTRIBUTION_NET", "ref_no": "N88192011", "debit": None, "credit": Decimal("15000000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "20-May-2025", "narration": "RTGS/INW/TATA_STRUCTURAL_INFRA_NEFT", "ref_no": "R11928374", "debit": None, "credit": Decimal("17500000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "10-Jun-2025", "narration": "ACH/DEBIT/SBI_TERM_LOAN_EMI/H1", "ref_no": "ACH339182", "debit": Decimal("1250000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": True, "is_tax": False},
            {"date": "12-Jul-2025", "narration": "CMS/COLL/AFCONS_INFRASTRUCTURE_PROJ", "ref_no": "CMS449102", "debit": None, "credit": Decimal("12500000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "15-Aug-2025", "narration": "CHQ RETURN: 092812 / FUNDS PENDING IN CLEARING", "ref_no": "RTN091", "debit": None, "credit": None, "is_bounce": True, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "15-Aug-2025", "narration": "CHQ RETURN PENAL CHARGE / INSUFFICIENT FUNDS", "ref_no": "CHG091", "debit": Decimal("590.00"), "credit": None, "is_bounce": False, "is_charge": True, "is_emi": False, "is_tax": False},
            {"date": "28-Aug-2025", "narration": "RTGS/OUT/SAIL_BILLETS_RAW_MATERIAL", "ref_no": "R44918201", "debit": Decimal("35000000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "18-Sep-2025", "narration": "TAX/GST_PAYMENT_CHLN_99120/STATUTORY_DISCHARGE", "ref_no": "CHL882190", "debit": Decimal("8100000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": True},
            {"date": "04-Oct-2025", "narration": "NEFT/INW/L_AND_T_HYDROCARBON_ENG", "ref_no": "N44910293", "debit": None, "credit": Decimal("13000000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "15-Nov-2025", "narration": "NEFT/OUT/MAHARASHTRA_POWER_GRID/FURNACE", "ref_no": "N99281048", "debit": Decimal("18000000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "08-Dec-2025", "narration": "ACH/DEBIT/SBI_TERM_LOAN_EMI/H2", "ref_no": "ACH772910", "debit": Decimal("1250000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": True, "is_tax": False},
            {"date": "22-Dec-2025", "narration": "RTGS/INW/DILIP_BUILDCON_HWY_SETTLE", "ref_no": "R88291029", "debit": None, "credit": Decimal("11000000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "14-Jan-2026", "narration": "RTGS/OUT/ALL_INDIA_LOGISTICS_CRANE_HIRE", "ref_no": "R33918290", "debit": Decimal("9399410.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "04-Feb-2026", "narration": "CMS/COLL/NCC_LIMITED_BRIDGE_PROJ", "ref_no": "CMS339182", "debit": None, "credit": Decimal("9500000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "25-Feb-2026", "narration": "NEFT/OUT/CONTRACT_LABOR_PAYOUT_MAHA", "ref_no": "N11029381", "debit": Decimal("12000000.00"), "credit": None, "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
            {"date": "18-Mar-2026", "narration": "RTGS/INW/GMR_INFRASTRUCTURE_METRO", "ref_no": "R99381029", "debit": None, "credit": Decimal("9500000.00"), "is_bounce": False, "is_charge": False, "is_emi": False, "is_tax": False},
        ],
        "gst": {
            "legal_name": "Bharat Mock Steels & Infra (Demo)",
            "trade_name": "Bharat Mock Steels & Infra (Demo)",
            "tax_period": "March 2026 (Annualized FY 2025-26 Summary)",
            "filing_date": "18-Apr-2026",
            "arn": "AA2703260081924",
            "taxable_value": format_inr(Decimal("45000000.00")),
            "igst_amount": format_inr(Decimal("4050000.00")),
            "cgst_amount": format_inr(Decimal("2025000.00")),
            "sgst_amount": format_inr(Decimal("2025000.00")),
            "total_tax_paid": format_inr(Decimal("8100000.00")),
        },
        "itr": {
            "assessment_year": "2026-27",
            "financial_year": "2025-26",
            "filing_date": "30-Sep-2026",
            "ack_number": "102938475610293",
            "gross_total_income": format_inr(Decimal("45000000.00")),
            "depreciation": format_inr(Decimal("1500000.00")),
            "interest_paid": format_inr(Decimal("2000000.00")),
            "profit_after_tax": format_inr(Decimal("3500000.00")),
            "net_worth": format_inr(Decimal("18000000.00")),
            "total_debt": format_inr(Decimal("14000000.00")),
            "existing_emi_obligations": format_inr(Decimal("2500000.00")),
        }
    }
}


def render_pdf_html(html_content: str, output_path: Path):
    """Renders HTML to PDF using Chrome Headless for pixel-perfect multi-page pagination."""
    if os.path.exists(CHROME_PATH):
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(html_content)
            temp_html = f.name
        try:
            cmd = [
                CHROME_PATH,
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={str(output_path)}",
                f"file://{temp_html}"
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finally:
            if os.path.exists(temp_html):
                os.remove(temp_html)
    else:
        raise RuntimeError(f"Chrome executable not found at: {CHROME_PATH}")


def main():
    print("=" * 70)
    print("SYNTHETIC DATA RECONCILIATION & VALIDATION ENGINE")
    print("=" * 70)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for cohort_id, data in COHORTS.items():
        cohort_dir = OUTPUT_DIR / cohort_id
        cohort_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n[COHORT] {cohort_id} -> {data['company_name']}")

        meta = data["bank_meta"]
        txns = data["transactions"]

        # 1. Exact mathematical checks
        ledger_credits = sum((t["credit"] for t in txns if t["credit"] is not None), Decimal("0.00"))
        ledger_debits = sum((t["debit"] for t in txns if t["debit"] is not None), Decimal("0.00"))

        print(f"  • Opening Balance    : ₹ {format_inr(meta['opening_balance'])}")
        print(f"  • Summary Credits    : ₹ {format_inr(meta['total_credits'])}")
        print(f"  • Ledger Credits Sum : ₹ {format_inr(ledger_credits)}")
        diff_credits = abs(ledger_credits - meta["total_credits"])
        assert diff_credits < Decimal("0.01"), f"Credit mismatch in {cohort_id}: {diff_credits}"

        print(f"  • Summary Debits     : ₹ {format_inr(meta['total_debits'])}")
        print(f"  • Ledger Debits Sum  : ₹ {format_inr(ledger_debits)}")
        diff_debits = abs(ledger_debits - meta["total_debits"])
        assert diff_debits < Decimal("0.01"), f"Debit mismatch in {cohort_id}: {diff_debits}"

        computed_closing = meta["opening_balance"] + ledger_credits - ledger_debits
        assert computed_closing == meta["closing_balance"], (
            f"Closing balance equation broken: {computed_closing} != {meta['closing_balance']}"
        )
        print(f"  • Closing Balance    : ₹ {format_inr(computed_closing)} (Equation Verified: Open + In - Out == Close)")

        # Prepare template context
        formatted_txns = []
        for t in txns:
            formatted_txns.append({
                "date": t["date"],
                "narration": t["narration"],
                "ref_no": t["ref_no"],
                "debit": format_inr(t["debit"]) if t["debit"] is not None else "",
                "credit": format_inr(t["credit"]) if t["credit"] is not None else "",
                "is_bounce": t["is_bounce"],
                "is_charge": t["is_charge"],
                "is_emi": t["is_emi"],
                "is_tax": t["is_tax"]
            })

        bank_template = env.get_template("bank_statement.html")
        bank_html = bank_template.render(
            bank_name=meta["bank_name"],
            branch_name=meta["branch_name"],
            ifsc_code=meta["ifsc_code"],
            statement_period=meta["statement_period"],
            account_number=meta["account_number"],
            address=meta["address"],
            company_name=data["company_name"],
            pan_or_cin=data["cin"],
            opening_balance=format_inr(meta["opening_balance"]),
            total_credits=format_inr(meta["total_credits"]),
            total_debits=format_inr(meta["total_debits"]),
            closing_balance=format_inr(meta["closing_balance"]),
            bounce_count=meta["bounce_count"],
            transactions=formatted_txns
        )

        bank_pdf = cohort_dir / "bank_statement.pdf"
        render_pdf_html(bank_html, bank_pdf)

        reader = PdfReader(str(bank_pdf))
        page_count = len(reader.pages)
        print(f"  • Rendered PDF       : {bank_pdf.name} (Pages: {page_count}, Size: {bank_pdf.stat().st_size} bytes)")
        assert page_count >= 2, f"Expected bank statement to have >= 2 pages, got {page_count}"

        # Render GSTR-3B
        gst_template = env.get_template("gstr3b.html")
        gst_html = gst_template.render(
            gstin=data["gstin"],
            **data["gst"]
        )
        gst_pdf = cohort_dir / "gstr3b.pdf"
        render_pdf_html(gst_html, gst_pdf)

        # Render ITR
        itr_template = env.get_template("itr_form.html")
        itr_html = itr_template.render(
            entity_name=data["company_name"],
            pan_number=data["pan"],
            cin_number=data["cin"],
            **data["itr"]
        )
        itr_pdf = cohort_dir / "itr_acknowledgement.pdf"
        render_pdf_html(itr_html, itr_pdf)

        # Write ground_truth.json for evaluation harness
        ground_truth = {
            "cohort": cohort_id,
            "company_name": data["company_name"],
            "cin": data["cin"],
            "pan": data["pan"],
            "gstin": data["gstin"],
            "bank": {
                "opening_balance": str(meta["opening_balance"]),
                "total_credits": str(meta["total_credits"]),
                "total_debits": str(meta["total_debits"]),
                "closing_balance": str(meta["closing_balance"]),
                "bounce_count": meta["bounce_count"]
            },
            "gst": {
                "taxable_value": str(Decimal(data["gst"]["taxable_value"].replace(",", ""))),
                "total_tax_paid": str(Decimal(data["gst"]["total_tax_paid"].replace(",", "")))
            },
            "itr": {
                "gross_total_income": str(Decimal(data["itr"]["gross_total_income"].replace(",", ""))),
                "profit_after_tax": str(Decimal(data["itr"]["profit_after_tax"].replace(",", ""))),
                "depreciation": str(Decimal(data["itr"]["depreciation"].replace(",", ""))),
                "interest_paid": str(Decimal(data["itr"]["interest_paid"].replace(",", ""))),
                "net_worth": str(Decimal(data["itr"]["net_worth"].replace(",", ""))),
                "total_debt": str(Decimal(data["itr"]["total_debt"].replace(",", ""))),
                "existing_emi_obligations": str(Decimal(data["itr"]["existing_emi_obligations"].replace(",", "")))
            }
        }
        with open(cohort_dir / "ground_truth.json", "w") as f:
            json.dump(ground_truth, f, indent=2)
        print(f"  • Generated Ground Truth: {cohort_dir / 'ground_truth.json'}")

    print("\n" + "=" * 70)
    print("ALL MATHEMATICAL RECONCILIATIONS AND PAGINATION CHECKS PASSED.")
    print("=" * 70)


if __name__ == "__main__":
    main()
