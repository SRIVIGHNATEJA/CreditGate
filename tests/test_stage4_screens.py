import io
import sys
from decimal import Decimal
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.parser import extract_all_documents
from core.scorer import score_credit_case
from core.policy import Verdict
from core.data_models import CreditCase, BankExtraction, GSTExtraction, ITRExtraction
from core.cam_generator import generate_cam_markdown
from ui.state import compute_composite_hash, COHORT_COMPANY_NAMES

APP_PATH = str(BASE_DIR / "app.py")
SYNTHETIC_DIR = BASE_DIR / "data" / "synthetic"


def test_stage4_all_three_cohorts_end_to_end():
    """
    Item 24: Verifies all 3 cohorts end to end:
    - Cohort 1: APPROVE
    - Cohort 2: REJECT via BOUNCE_KNOCKOUT
    - Cohort 3: CONDITIONAL via WEAKEST_LINK_CAP
    """
    # 1. Cohort 1
    c1 = SYNTHETIC_DIR / "cohort_1_prime"
    c1_case, _ = extract_all_documents(
        (c1 / "bank_statement.pdf").read_bytes(),
        (c1 / "gstr3b.pdf").read_bytes(),
        (c1 / "itr_acknowledgement.pdf").read_bytes(),
        company_name="Apex Synthetic Holdings (Demo)",
        cin_or_pan="U29253DL2018PTC334120",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("164993.54"),
        proposed_rate=Decimal("11.50"),
        proposed_tenure_months=36,
    )
    r1 = score_credit_case(c1_case, business_vintage_years=6, collateral_value=Decimal("0"), cgtmse_covered=True)
    assert r1.final_verdict == Verdict.APPROVE
    assert r1.knockout_applied is None
    assert r1.cap_applied is None

    # 2. Cohort 2
    c2 = SYNTHETIC_DIR / "cohort_2_distressed"
    c2_case, _ = extract_all_documents(
        (c2 / "bank_statement.pdf").read_bytes(),
        (c2 / "gstr3b.pdf").read_bytes(),
        (c2 / "itr_acknowledgement.pdf").read_bytes(),
        company_name="Kaveri Fictitious Freight (Demo)",
        cin_or_pan="U60200KA2019PTC120481",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("164993.54"),
        proposed_rate=Decimal("11.50"),
        proposed_tenure_months=36,
    )
    r2 = score_credit_case(c2_case, business_vintage_years=5, collateral_value=Decimal("0"), cgtmse_covered=True)
    assert r2.final_verdict == Verdict.REJECT
    assert r2.knockout_applied is not None
    assert "BOUNCE_KNOCKOUT" in r2.knockout_applied

    # 3. Cohort 3
    c3 = SYNTHETIC_DIR / "cohort_3_mismatch"
    c3_case, _ = extract_all_documents(
        (c3 / "bank_statement.pdf").read_bytes(),
        (c3 / "gstr3b.pdf").read_bytes(),
        (c3 / "itr_acknowledgement.pdf").read_bytes(),
        company_name="Bharat Mock Steels & Infra (Demo)",
        cin_or_pan="U24101MH2020PTC349120",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("164993.54"),
        proposed_rate=Decimal("11.50"),
        proposed_tenure_months=36,
    )
    r3 = score_credit_case(c3_case, business_vintage_years=4, collateral_value=Decimal("0"), cgtmse_covered=True)
    assert r3.final_verdict == Verdict.CONDITIONAL
    assert r3.cap_applied is not None
    assert "WEAKEST_LINK_CAP" in r3.cap_applied


def test_stage4_uploading_cohort3_pdfs_gives_conditional():
    """
    Item 24: Verifies uploading the 3 Cohort 3 PDFs gives CONDITIONAL, the same as the sample-card path.
    """
    c3 = SYNTHETIC_DIR / "cohort_3_mismatch"
    uploaded_b = (c3 / "bank_statement.pdf").read_bytes()
    uploaded_g = (c3 / "gstr3b.pdf").read_bytes()
    uploaded_it = (c3 / "itr_acknowledgement.pdf").read_bytes()

    case, meta = extract_all_documents(
        bank_bytes=uploaded_b,
        gst_bytes=uploaded_g,
        itr_bytes=uploaded_it,
        company_name="Manual Applicant Company",
        cin_or_pan="U24101MH2020PTC349120",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("164993.54"),
        proposed_rate=Decimal("11.50"),
        proposed_tenure_months=36,
    )

    res = score_credit_case(
        credit_case=case,
        business_vintage_years=4,
        collateral_value=Decimal("0"),
        cgtmse_covered=True,
    )

    assert res.final_verdict == Verdict.CONDITIONAL
    assert res.cap_applied is not None
    assert "WEAKEST_LINK_CAP" in res.cap_applied


def test_stage4_uploading_non_matching_pdf_routes_to_insufficient_data():
    """
    Item 24: Uploading a non-matching PDF does not crash and routes to INSUFFICIENT_DATA.
    """
    non_matching_text = (
        "This is an arbitrary company overview document.\n"
        "No balance sheet, banking transactions, or GST returns are contained herein.\n"
        "General corporate summary."
    )
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), non_matching_text)
    non_matching_pdf = doc.tobytes()
    doc.close()

    case, meta = extract_all_documents(
        bank_bytes=non_matching_pdf,
        gst_bytes=non_matching_pdf,
        itr_bytes=non_matching_pdf,
        company_name="Non Matching Applicant",
        cin_or_pan="U12345DL2020PTC123456",
        requested_loan_amount=Decimal("1000000.00"),
        proposed_emi=Decimal("35000.00"),
        proposed_rate=Decimal("12.0"),
        proposed_tenure_months=36,
    )

    res = score_credit_case(case, business_vintage_years=5)
    assert res.final_verdict == Verdict.INSUFFICIENT_DATA
    assert len(res.policy_reasons) > 0


def test_stage4_partial_or_malformed_document():
    """
    Item 24: Verifies partial/malformed document handling routes to INSUFFICIENT_DATA safely without crashing.
    """
    malformed_pdf = b"NOT_A_VALID_PDF_HEADER_CONTENT_TRUNCATED"
    c1 = SYNTHETIC_DIR / "cohort_1_prime"
    valid_bank = (c1 / "bank_statement.pdf").read_bytes()

    case, meta = extract_all_documents(
        bank_bytes=valid_bank,
        gst_bytes=malformed_pdf,
        itr_bytes=malformed_pdf,
        company_name="Partial Case (Demo)",
        cin_or_pan="U29253DL2018PTC334120",
    )

    res = score_credit_case(case, business_vintage_years=5)
    assert res.final_verdict == Verdict.INSUFFICIENT_DATA


def test_stage4_pan_change_makes_score_stale():
    """
    Item 24: Verifies PAN change -> score goes stale via composite hash comparison.
    """
    initial_hash = compute_composite_hash(
        locked_financials_hash="hash_fin_001",
        loan_amount=5000000.0,
        tenure_months=36,
        interest_rate=11.5,
        proposed_emi=164993.54,
        collateral_val=0.0,
        cgtmse_covered=True,
        cin="U29253DL2018PTC334120",
        vintage=6,
        pan="AAACA1234B",
        pan_mismatch=False,
        research_findings=[],
    )

    # Change PAN to personal/inconsistent PAN
    changed_hash = compute_composite_hash(
        locked_financials_hash="hash_fin_001",
        loan_amount=5000000.0,
        tenure_months=36,
        interest_rate=11.5,
        proposed_emi=164993.54,
        collateral_val=0.0,
        cgtmse_covered=True,
        cin="U29253DL2018PTC334120",
        vintage=6,
        pan="AAAPA9999Z",  # Personal PAN causes entity mismatch
        pan_mismatch=True,
        research_findings=[],
    )

    assert initial_hash != changed_hash


def test_stage4_cohort_switch_leaves_no_stale_state():
    """
    Item 24: Verifies cohort switch -> no stale state remains in session state.
    """
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run(timeout=30)

    # Mount Cohort 1
    next(b for b in at.button if b.key == "btn_open_prime").click().run(timeout=30)
    at.switch_page("app_pages/evidence.py").run(timeout=30)
    for btn in at.button:
        if "Extract" in btn.label:
            btn.click().run(timeout=30)
            break
    for btn in at.button:
        if "Confirm & Lock" in btn.label:
            btn.click().run(timeout=30)
            break
    at.switch_page("app_pages/decision.py").run(timeout=30)
    for btn in at.button:
        if "Run Deterministic" in btn.label:
            btn.click().run(timeout=30)
            break
    assert at.session_state.score_result is not None

    # Switch to Cohort 2 on Case page
    at.switch_page("app_pages/case.py").run(timeout=30)
    next(b for b in at.button if b.key == "btn_open_distressed").click().run(timeout=30)

    # Downstream extraction, hitl, locked_case, and score_result must be wiped cleanly
    assert at.session_state.score_result is None
    assert at.session_state.locked_case is None
    assert at.session_state.extracted_case is None
    assert at.session_state.editable_df is None
    assert at.session_state.hitl_confirmed is False


def test_stage4_cam_has_correct_weights_separate_pan_cin_masked_pan():
    """
    Item 24: Verifies CAM has the correct weights (20/30/20/15/15),
    separate PAN/CIN sections, and masked PAN.
    """
    c1 = SYNTHETIC_DIR / "cohort_1_prime"
    c1_case, _ = extract_all_documents(
        (c1 / "bank_statement.pdf").read_bytes(),
        (c1 / "gstr3b.pdf").read_bytes(),
        (c1 / "itr_acknowledgement.pdf").read_bytes(),
        company_name="Apex Synthetic Holdings (Demo)",
        cin_or_pan="U29253DL2018PTC334120",
    )
    score_res = score_credit_case(c1_case, business_vintage_years=6)

    cam_md = generate_cam_markdown(
        credit_case=c1_case,
        score_result=score_res,
        cin_parsed_data={"cin": "U29253DL2018PTC334120", "macro_sector": "Manufacturing", "business_vintage_years": 6, "state_name": "Delhi"},
        officer_notes=[],
        executive_override=None,
        audit_events=[],
        adverse_media_report=None,
        app_id="APP-TEST-STAGE4",
        pan="AAACA1234B",
    )

    # 1. Weights
    assert "20%" in cam_md  # Character
    assert "30%" in cam_md  # Capacity
    assert "15%" in cam_md  # Collateral / Conditions

    # 2. PAN and CIN separate
    assert "CIN:" in cam_md or "Corporate Identification Number" in cam_md
    assert "PAN:" in cam_md or "Permanent Account Number" in cam_md

    # 3. Masked PAN (raw PAN AAACA1234B must not appear unmasked)
    assert "AAACA1234B" not in cam_md
    assert "AA***4B" in cam_md
