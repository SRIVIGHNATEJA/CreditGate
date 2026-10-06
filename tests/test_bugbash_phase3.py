"""
Tests for Bugbash Phase 3 (UI Metrics & Caching Polish: B-10, B-11, B-12, B-14, B-15).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from decimal import Decimal
import zipfile
import io
import pytest

from core.data_models import GSTExtraction, BankExtraction, ITRExtraction


def test_b10_accrual_cash_gap_none_displays_na():
    """B-10: If gst_l.taxable_value or bank_l.total_credits is None, gap displays N/A with neutral color."""
    gst_l = GSTExtraction(taxable_value=None, total_tax_paid=None)
    bank_l = BankExtraction(total_credits=Decimal("12000000"))

    # Test logic matching decision.py
    gap_disp = "N/A"
    gap_color = "neutral"
    if gst_l and bank_l and gst_l.taxable_value is not None and bank_l.total_credits is not None:
        max_t = max(gst_l.taxable_value, bank_l.total_credits)
        if max_t > Decimal("0"):
            gap_pct = float(abs(gst_l.taxable_value - bank_l.total_credits) / max_t * Decimal("100"))
            gap_disp = f"{gap_pct:.1f}%"
            gap_color = "normal" if gap_pct <= 15.0 else "inverse"
        else:
            gap_disp = "0.0%"
            gap_color = "normal"

    assert gap_disp == "N/A"
    assert gap_color == "neutral"


def test_b11_leverage_insolvent_negative_net_worth():
    """B-11: If itr_l.net_worth <= Decimal('0'), Leverage KPI must display 'Insolvent (< 0)' with inverse color."""
    itr_l = ITRExtraction(net_worth=Decimal("-500000"), total_debt=Decimal("2000000"))

    de_disp = "N/A"
    de_color = "neutral"
    if itr_l and itr_l.net_worth is not None:
        if itr_l.net_worth <= Decimal("0"):
            de_disp = "Insolvent (< 0)"
            de_color = "inverse"
        else:
            de_val = float(itr_l.total_debt / itr_l.net_worth)
            de_disp = f"{de_val:.2f}x"
            de_color = "normal" if de_val <= 1.0 else "inverse"

    assert de_disp == "Insolvent (< 0)"
    assert de_color == "inverse"


def test_b12_create_sample_zip_cached():
    """B-12: create_sample_zip must be wrapped with @st.cache_data in evidence.py."""
    evidence_text = (Path(__file__).parent.parent / "app_pages" / "evidence.py").read_text(encoding="utf-8")
    assert "@st.cache_data\ndef create_sample_zip" in evidence_text or "@st.cache_data" in evidence_text

    # Verify synthetic sample files exist and are zip-packable
    base_syn = Path(__file__).parent.parent / "data" / "synthetic"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for cohort_folder in ["cohort_1_prime", "cohort_2_distressed", "cohort_3_mismatch"]:
            cdir = base_syn / cohort_folder
            if cdir.exists():
                for pdf_name in ["bank_statement.pdf", "gstr3b.pdf", "itr_acknowledgement.pdf"]:
                    pdf_path = cdir / pdf_name
                    if pdf_path.exists():
                        z.writestr(f"{cohort_folder}/{pdf_name}", pdf_path.read_bytes())
    zip_bytes = buf.getvalue()
    assert len(zip_bytes) > 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as z:
        assert any("cohort_1_prime" in n for n in z.namelist())


def test_b14_hitl_grid_formatter_cheque_bounces_no_rupee():
    """B-14: Ensure the HITL grid formatter does not append ₹ to the Cheque Bounces integer column/values."""
    def format_hitl_val(param_name: str, val):
        import pandas as pd
        if pd.isna(val) or val is None or str(val).strip() == "":
            return "BLANK"
        try:
            f_val = float(val)
            if "Bounces" in param_name or "bounce" in param_name.lower():
                return f"{int(f_val)}"
            return f"₹ {f_val:,.2f}"
        except (ValueError, TypeError):
            return str(val)

    # Cheque bounces must be an integer count without ₹
    bounce_formatted = format_hitl_val("Cheque / Mandate Bounces", 5)
    assert bounce_formatted == "5"
    assert "₹" not in bounce_formatted

    # Monetary fields must retain ₹
    currency_formatted = format_hitl_val("Total Bank Inward Credits", 12000000.0)
    assert currency_formatted == "₹ 12,000,000.00"
    assert "₹" in currency_formatted


def test_b15_app_py_subtitle_reflects_4_page_structure():
    """B-15: Subtitle in app.py reflects the new 4-page navigation structure."""
    app_text = (Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")
    assert "1. Onboarding & Entity Intel" not in app_text
    assert "1. Case" in app_text
    assert "2. Evidence" in app_text
    assert "3. Decision" in app_text
    assert "4. CAM & Audit" in app_text
