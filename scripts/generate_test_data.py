import os
import sys
import subprocess
import tempfile
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

# Project base paths
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "data" / "synthetic"

env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

COHORTS = {
    "cohort_1_prime": {
        "company_name": "Apex Precision Tools Pvt Ltd",
        "cin": "U29253DL2018PTC334120",
        "pan": "AAACA1234B",
        "gstin": "07AAACA1234B1Z5",
        "bank": {
            "bank_name": "HDFC Bank Ltd",
            "branch_name": "Industrial Finance Branch, Okhla",
            "ifsc_code": "HDFC0000128",
            "statement_period": "01-Apr-2025 to 31-Mar-2026",
            "account_number": "50200019283741",
            "address": "Plot 42, Okhla Industrial Area Ph-III, New Delhi 110020",
            "opening_balance": "45,20,500.00",
            "total_credits": "12,50,00,000.00",
            "total_debits": "11,80,00,000.00",
            "closing_balance": "1,15,20,500.00",
            "bounce_count": 0,
            "transactions": [
                {"date": "04-Apr-2025", "narration": "NEFT/INW/BOSCH_INDIA_LTD/INV882", "ref_no": "N09482103", "debit": "", "credit": "45,00,000.00", "is_bounce": False},
                {"date": "18-May-2025", "narration": "RTGS/OUT/STEEL_SUPPLIERS_CORP", "ref_no": "R00291823", "debit": "38,50,000.00", "credit": "", "is_bounce": False},
                {"date": "12-Aug-2025", "narration": "NEFT/INW/TATA_ADVANCED_SYS/ADV", "ref_no": "N11029381", "debit": "", "credit": "78,20,000.00", "is_bounce": False},
                {"date": "15-Dec-2025", "narration": "CMS/COLL/DOMESTIC_CLIENT_PAYMENTS", "ref_no": "CMS99210", "debit": "", "credit": "92,50,000.00", "is_bounce": False},
                {"date": "20-Mar-2026", "narration": "TAX/GST_PAYMENT_CHLN_09210", "ref_no": "CHL882190", "debit": "22,40,000.00", "credit": "", "is_bounce": False}
            ]
        },
        "gst": {
            "legal_name": "Apex Precision Tools Private Limited",
            "trade_name": "Apex Precision Tools",
            "tax_period": "March 2026 (Annualized FY 2025-26 Summary)",
            "filing_date": "20-Apr-2026",
            "arn": "AA0703260019283",
            "taxable_value": "12,45,00,000.00",
            "igst_amount": "1,12,05,000.00",
            "cgst_amount": "56,02,500.00",
            "sgst_amount": "56,02,500.00",
            "total_tax_paid": "2,24,10,000.00"
        },
        "itr": {
            "assessment_year": "2026-27",
            "financial_year": "2025-26",
            "filing_date": "15-Sep-2026",
            "ack_number": "928301928471029",
            "gross_total_income": "12,45,00,000.00",
            "depreciation": "30,00,000.00",
            "interest_paid": "25,00,000.00",
            "profit_after_tax": "1,20,00,000.00",
            "net_worth": "6,50,00,000.00",
            "total_debt": "1,80,00,000.00",
            "existing_emi_obligations": "40,00,000.00"
        }
    },
    "cohort_2_distressed": {
        "company_name": "Kaveri Logistics & Freight Pvt Ltd",
        "cin": "U60200KA2019PTC120481",
        "pan": "AAACK5678M",
        "gstin": "29AAACK5678M1Z2",
        "bank": {
            "bank_name": "State Bank of India",
            "branch_name": "Commercial Branch, Peenya, Bengaluru",
            "ifsc_code": "SBIN0004120",
            "statement_period": "01-Apr-2025 to 31-Mar-2026",
            "account_number": "38291049281",
            "address": "Plot 88, 4th Phase, Peenya Industrial Area, Bengaluru 560058",
            "opening_balance": "8,10,200.00",
            "total_credits": "4,20,00,000.00",
            "total_debits": "4,18,00,000.00",
            "closing_balance": "10,10,200.00",
            "bounce_count": 5,
            "transactions": [
                {"date": "14-May-2025", "narration": "CHQ RTN: 482910 / INSUFFICIENT FUNDS", "ref_no": "RTN001", "debit": "750.00", "credit": "", "is_bounce": True},
                {"date": "22-Jul-2025", "narration": "ECS RETURN: VEHICLE LOAN HDFC", "ref_no": "RTN002", "debit": "1,000.00", "credit": "", "is_bounce": True},
                {"date": "11-Sep-2025", "narration": "CHQ RTN: 882912 / BALANCE EXCEEDED", "ref_no": "RTN003", "debit": "750.00", "credit": "", "is_bounce": True},
                {"date": "19-Nov-2025", "narration": "INWARD CLG RET: DIXON LOGISTICS", "ref_no": "RTN004", "debit": "1,000.00", "credit": "", "is_bounce": True},
                {"date": "05-Feb-2026", "narration": "NACH RET: AXIS BANK BUS LOAN", "ref_no": "RTN005", "debit": "1,000.00", "credit": "", "is_bounce": True}
            ]
        },
        "gst": {
            "legal_name": "Kaveri Logistics & Freight Private Limited",
            "trade_name": "Kaveri Freight Lines",
            "tax_period": "March 2026 (Annualized FY 2025-26 Summary)",
            "filing_date": "28-Apr-2026",
            "arn": "AA2903260049182",
            "taxable_value": "4,10,00,000.00",
            "igst_amount": "49,20,000.00",
            "cgst_amount": "12,30,000.00",
            "sgst_amount": "12,30,000.00",
            "total_tax_paid": "73,80,000.00"
        },
        "itr": {
            "assessment_year": "2026-27",
            "financial_year": "2025-26",
            "filing_date": "29-Oct-2026",
            "ack_number": "819204819284102",
            "gross_total_income": "4,10,00,000.00",
            "depreciation": "10,00,000.00",
            "interest_paid": "35,00,000.00",
            "profit_after_tax": "12,00,000.00",
            "net_worth": "40,00,000.00",
            "total_debt": "3,20,00,000.00",
            "existing_emi_obligations": "45,00,000.00"
        }
    },
    "cohort_3_mismatch": {
        "company_name": "Bharat Steels & Infrastructure LLP",
        "cin": "AAB-4921",
        "pan": "AABFB8910N",
        "gstin": "27AABFB8910N1Z8",
        "bank": {
            "bank_name": "ICICI Bank Ltd",
            "branch_name": "Nariman Point Branch, Mumbai",
            "ifsc_code": "ICIC0000004",
            "statement_period": "01-Apr-2025 to 31-Mar-2026",
            "account_number": "000405018291",
            "address": "1204, Maker Chambers V, Nariman Point, Mumbai 400021",
            "opening_balance": "15,80,000.00",
            "total_credits": "8,80,00,000.00",
            "total_debits": "8,50,00,000.00",
            "closing_balance": "45,80,000.00",
            "bounce_count": 1,
            "transactions": [
                {"date": "10-Jun-2025", "narration": "NEFT/INW/JSW_STEEL_DISTRIBUTION", "ref_no": "N881920", "debit": "", "credit": "1,20,00,000.00", "is_bounce": False},
                {"date": "15-Aug-2025", "narration": "CHQ RETURN: 092812 / FUNDS PENDING", "ref_no": "RTN091", "debit": "750.00", "credit": "", "is_bounce": True},
                {"date": "12-Jan-2026", "narration": "RTGS/INW/INFRA_PROJECTS_MAHA", "ref_no": "R991023", "debit": "", "credit": "2,40,00,000.00", "is_bounce": False}
            ]
        },
        "gst": {
            "legal_name": "Bharat Steels & Infrastructure LLP",
            "trade_name": "Bharat Steels",
            "tax_period": "March 2026 (Annualized FY 2025-26 Summary)",
            "filing_date": "18-Apr-2026",
            "arn": "AA2703260081924",
            "taxable_value": "4,50,00,000.00",
            "igst_amount": "40,50,000.00",
            "cgst_amount": "20,25,000.00",
            "sgst_amount": "20,25,000.00",
            "total_tax_paid": "81,00,000.00"
        },
        "itr": {
            "assessment_year": "2026-27",
            "financial_year": "2025-26",
            "filing_date": "30-Sep-2026",
            "ack_number": "102938475610293",
            "gross_total_income": "4,50,00,000.00",
            "depreciation": "15,00,000.00",
            "interest_paid": "20,00,000.00",
            "profit_after_tax": "35,00,000.00",
            "net_worth": "1,80,00,000.00",
            "total_debt": "1,40,00,000.00",
            "existing_emi_obligations": "25,00,000.00"
        }
    }
}


def render_pdf_html(html_content: str, output_path: Path):
    """Renders HTML string to PDF using WeasyPrint if present, or native Chrome Headless print-to-pdf."""
    try:
        from weasyprint import HTML
        HTML(string=html_content).write_pdf(str(output_path))
        return
    except Exception:
        pass

    # High-fidelity Chrome headless fallback (DOM to PDF)
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
        raise RuntimeError("Neither WeasyPrint nor Google Chrome headless is available for PDF rendering.")


def main():
    print(f"Starting synthetic document generation in: {OUTPUT_DIR}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for cohort_id, data in COHORTS.items():
        cohort_dir = OUTPUT_DIR / cohort_id
        cohort_dir.mkdir(parents=True, exist_ok=True)
        print(f"\nProcessing {cohort_id} ({data['company_name']})...")

        # 1. Bank Statement
        bank_template = env.get_template("bank_statement.html")
        bank_html = bank_template.render(
            company_name=data["company_name"],
            pan_or_cin=data["cin"],
            **data["bank"]
        )
        bank_pdf_path = cohort_dir / "bank_statement.pdf"
        render_pdf_html(bank_html, bank_pdf_path)
        print(f"  [OK] Rendered: {bank_pdf_path.name} ({bank_pdf_path.stat().st_size} bytes)")

        # 2. GSTR-3B
        gst_template = env.get_template("gstr3b.html")
        gst_html = gst_template.render(
            gstin=data["gstin"],
            **data["gst"]
        )
        gst_pdf_path = cohort_dir / "gstr3b.pdf"
        render_pdf_html(gst_html, gst_pdf_path)
        print(f"  [OK] Rendered: {gst_pdf_path.name} ({gst_pdf_path.stat().st_size} bytes)")

        # 3. ITR Schedule
        itr_template = env.get_template("itr_form.html")
        itr_html = itr_template.render(
            entity_name=data["company_name"],
            pan_number=data["pan"],
            cin_number=data["cin"],
            **data["itr"]
        )
        itr_pdf_path = cohort_dir / "itr_acknowledgement.pdf"
        render_pdf_html(itr_html, itr_pdf_path)
        print(f"  [OK] Rendered: {itr_pdf_path.name} ({itr_pdf_path.stat().st_size} bytes)")

    print("\n[SUCCESS] All 3 cohorts (9 high-fidelity financial PDFs) successfully generated.")


if __name__ == "__main__":
    main()
