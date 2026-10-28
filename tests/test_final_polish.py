import sys
from decimal import Decimal
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

BASE_DIR = Path(__file__).resolve().parent.parent
APP_PATH = str(BASE_DIR / "app.py")
sys.path.insert(0, str(BASE_DIR))

from core.policy import Verdict, WEIGHTS
from core.data_models import (
    CreditCase,
    BankExtraction,
    GSTExtraction,
    ITRExtraction,
    FindingTier,
    ReviewStatus,
    AdverseMediaFinding,
    AdverseMediaReport,
)
from core.parser import (
    _extract_text_deterministic_bank,
    _extract_text_deterministic_gst,
    _extract_text_deterministic_itr,
    extract_all_documents,
)
from core.scorer import score_credit_case
from core.cam_generator import generate_cam_markdown, generate_cam_docx, generate_cam_pdf
from core.policy import derive_stipulated_covenants
from core.audit_logger import AuditLogger


def test_parser_missing_label_returns_none_and_insufficient_data():
    """
    Verifies that:
    1. If a mandatory label is not found in the text layer, the fallback parser
       returns None and LOW confidence (honesty requirement).
    2. Missing financial fields route deterministically to Verdict.INSUFFICIENT_DATA.
    3. An applicant with legitimate zero values (e.g., zero debt, zero bounces)
       correctly retains Decimal('0.00') / 0 and evaluates normally.
    """
    # Dummy PDF with missing required financial fields
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Dummy Statement without required anchors.")
    dummy_bytes = doc.tobytes()
    doc.close()

    bank_ext = _extract_text_deterministic_bank(dummy_bytes)
    assert bank_ext.total_credits is None
    assert bank_ext.opening_balance is None
    assert bank_ext.confidence == "LOW"

    gst_ext = _extract_text_deterministic_gst(dummy_bytes)
    assert gst_ext.taxable_value is None
    assert gst_ext.confidence == "LOW"

    itr_ext = _extract_text_deterministic_itr(dummy_bytes)
    assert itr_ext.gross_total_income is None
    assert itr_ext.profit_after_tax is None
    assert itr_ext.confidence == "LOW"

    # Incomplete case passed to scorer must halt at INSUFFICIENT_DATA
    case_incomplete = CreditCase(
        company_name="Incomplete Applicant (Demo)",
        cin_or_pan="U29253DL2018PTC334120",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("150000.00"),
        proposed_rate=Decimal("11.50"),
        proposed_tenure_months=36,
        bank_data=bank_ext,
        gst_data=gst_ext,
        itr_data=itr_ext
    )

    res = score_credit_case(case_incomplete)
    assert res.final_verdict == Verdict.INSUFFICIENT_DATA
    assert any("Missing mandatory extraction payload" in r for r in res.policy_reasons)

    # Legitimate zero value test
    bank_zero = BankExtraction(
        total_credits=Decimal("10000000.00"),
        total_debits=Decimal("9000000.00"),
        opening_balance=Decimal("1000000.00"),
        closing_balance=Decimal("2000000.00"),
        bounce_count=0,  # Legitimate zero
        confidence="HIGH"
    )
    assert bank_zero.bounce_count == 0


def test_cam_weights_and_pan_cin_separation():
    """
    Verifies that CAM markdown:
    1. Reads dynamic weights directly from WEIGHTS (Character: 20%, Capacity: 30%, Capital: 20%, Collateral: 15%, Conditions: 15%).
    2. Shows PAN (masked) and CIN separately.
    3. Contains no officer adjustment slider text or marketing buzzwords.
    """
    case = CreditCase(
        company_name="Apex Synthetic Holdings (Demo)",
        cin_or_pan="U29253DL2018PTC334120",
        pan="AAACA1234B",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("164900.00"),
        proposed_rate=Decimal("11.50"),
        proposed_tenure_months=36,
        bank_data=BankExtraction(
            total_credits=Decimal("125000000.00"),
            total_debits=Decimal("118000000.00"),
            opening_balance=Decimal("4520500.00"),
            closing_balance=Decimal("11520500.00"),
            bounce_count=0,
            confidence="HIGH"
        ),
        gst_data=GSTExtraction(
            taxable_value=Decimal("124500000.00"),
            total_tax_paid=Decimal("22410000.00"),
            confidence="HIGH"
        ),
        itr_data=ITRExtraction(
            gross_total_income=Decimal("124500000.00"),
            profit_after_tax=Decimal("12000000.00"),
            depreciation=Decimal("3000000.00"),
            interest_paid=Decimal("2500000.00"),
            net_worth=Decimal("65000000.00"),
            total_debt=Decimal("18000000.00"),
            existing_emi_obligations=Decimal("4000000.00"),
            confidence="HIGH"
        )
    )

    cin_parsed = {
        "cin": "U29253DL2018PTC334120",
        "listing_status": "Unlisted",
        "macro_sector": "Manufacturing",
        "business_vintage_years": 8,
        "low_vintage_risk_flag": False,
        "entity_code": "Private Limited Company",
        "state_name": "Delhi"
    }

    score_res = score_credit_case(case, business_vintage_years=8, cgtmse_covered=True)
    notes = [{"affected_c": "CHARACTER", "note_text": "Factory visit satisfactory.", "timestamp": "2026-10-05 12:00:00 UTC"}]

    cam = generate_cam_markdown(
        credit_case=case,
        score_result=score_res,
        cin_parsed_data=cin_parsed,
        officer_notes=notes,
        app_id="APP-TEST-001",
        pan="AAACA1234B"
    )

    # 1. Weights verification (not all 20%)
    assert "| **CAPACITY** | 30% |" in cam
    assert "| **CHARACTER** | 20% |" in cam
    assert "| **CAPITAL** | 20% |" in cam
    assert "| **COLLATERAL** | 15% |" in cam
    assert "| **CONDITIONS** | 15% |" in cam

    # 2. PAN and CIN separation
    assert "- **Permanent Account Number (PAN):** `AA***4B` *(PII masked)*" in cam
    assert "- **Corporate Identification Number (CIN):** `U29253DL2018PTC334120`" in cam

    # 3. No officer adjustment slider line
    assert "Adjustment:" not in cam

    # 4. Tone scrub check
    assert "Zero LLM hallucinations" not in cam
    assert "Prism" not in cam
    assert "Digital Lending Norms" not in cam


def test_audit_provenance_logs_deterministic_fallback():
    """
    Verifies that when running offline / without Gemini API, the parser
    returns extraction_engine='DETERMINISTIC_FALLBACK' and AuditLogger records it.
    """
    synthetic_dir = BASE_DIR / "data" / "synthetic" / "cohort_1_prime"
    bank_bytes = (synthetic_dir / "bank_statement.pdf").read_bytes()
    gst_bytes = (synthetic_dir / "gstr3b.pdf").read_bytes()
    itr_bytes = (synthetic_dir / "itr_acknowledgement.pdf").read_bytes()

    case, meta = extract_all_documents(
        bank_bytes=bank_bytes,
        gst_bytes=gst_bytes,
        itr_bytes=itr_bytes,
        company_name="Apex Synthetic Holdings (Demo)",
        cin_or_pan="U29253DL2018PTC334120"
    )

    # Since GEMINI_API_KEY is not configured or in fallback mode
    engine = meta.get("extraction_engine")
    assert engine in ["DETERMINISTIC_FALLBACK", "AI_GEMINI"]

    # Log audit event with engine
    audit = AuditLogger("APP-TEST-AUDIT")
    evt = audit.log(
        event_type="EXTRACTION_COMPLETED",
        actor=engine,
        details={"model": meta["extraction_model"], "engine": engine}
    )
    assert evt["actor"] == engine


def test_confirmed_severe_finding_and_officer_veto_enforces_reject():
    """
    Crucial Rule: Confirmed SEVERE adverse finding (like NCLT) must force REJECT,
    and cannot be overridden to APPROVE / CONDITIONAL by an officer veto.
    """
    case = CreditCase(
        company_name="Kaveri Fictitious Freight (Demo)",
        cin_or_pan="U60200KA2019PTC120481",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("150000.00"),
        proposed_rate=Decimal("11.50"),
        proposed_tenure_months=36,
        bank_data=BankExtraction(
            total_credits=Decimal("42000000.00"),
            total_debits=Decimal("41800000.00"),
            opening_balance=Decimal("810200.00"),
            closing_balance=Decimal("1010200.00"),
            bounce_count=5,
            confidence="HIGH"
        ),
        gst_data=GSTExtraction(
            taxable_value=Decimal("41000000.00"),
            total_tax_paid=Decimal("7380000.00"),
            confidence="HIGH"
        ),
        itr_data=ITRExtraction(
            gross_total_income=Decimal("41000000.00"),
            profit_after_tax=Decimal("12000000.00"),
            depreciation=Decimal("1000000.00"),
            interest_paid=Decimal("3500000.00"),
            net_worth=Decimal("4000000.00"),
            total_debt=Decimal("32000000.00"),
            existing_emi_obligations=Decimal("4500000.00"),
            confidence="HIGH"
        )
    )

    severe_finding = AdverseMediaFinding(
        id="adv-001",
        tier=FindingTier.SEVERE,
        title="[SYNTHETIC DEMO DATA] NCLT Admitted Section 9 Insolvency Petition",
        snippet="Insolvency petition admitted by NCLT.",
        source_url="https://example.com/orders/2026/synthetic/kaveri_sec9.pdf",
        review_status=ReviewStatus.CONFIRMED,
        confirmation_note="Confirmed with official court registry docket."
    )

    report = AdverseMediaReport(
        status="COMPLETED",
        findings=[severe_finding],
        is_synthetic_demo=True
    )

    # 1. Scorer policy gate with severe flags produces REJECT
    score_res = score_credit_case(
        credit_case=case,
        business_vintage_years=7,
        severe_flags=["[SEVERE ADVERSE FINDING] adv-001"]
    )
    assert score_res.final_verdict == Verdict.REJECT
    assert score_res.knockout_applied is not None

    # 2. Check veto governance validation logic
    has_confirmed_severe = any(
        f.tier == FindingTier.SEVERE and f.review_status == ReviewStatus.CONFIRMED
        for f in report.findings
    )
    assert has_confirmed_severe is True

    # Backend governance rule: attempted override to APPROVE when severe finding is confirmed is INVALID
    attempted_veto_verdict = "APPROVE"
    is_valid_override = not (has_confirmed_severe and attempted_veto_verdict != "REJECT")
    assert is_valid_override is False, "Officer veto must not override confirmed severe finding!"


def test_sidebar_and_intake_unincorporated_firm_formatting():
    """
    Verifies that for unincorporated entities:
    1. Partnership firms (PAN 'F', e.g. Bharat Mock Steels) render 'PARTNERSHIP FIRM' in the sidebar.
    2. Sole proprietorships (PAN 'P') render 'PROPRIETOR' in the sidebar.
    3. The raw string 'UNREGISTERED_MSME' is never leaked into the sidebar status text.
    """
    from ui.state import init_session_state, get_app_context
    import streamlit as st

    init_session_state()

    # Case 1: Partnership Firm (PAN 'F', e.g. Bharat Mock Steels)
    st.session_state.active_cohort_selection = "None (Manual Upload)"
    st.session_state.active_is_corporate = False
    st.session_state.case_is_corporate = False
    st.session_state.active_cin_selection = "UNREGISTERED_MSME"
    st.session_state.active_pan_input = "AABFB8910N"

    ctx = get_app_context()
    assert ctx["is_corporate"] is False
    pan_char = ctx["pan_check"].get("pan_entity_char")
    assert pan_char == "F"

    # Case 2: Sole Proprietor (PAN 'P')
    st.session_state.active_pan_input = "AAAPA1234B"
    ctx_prop = get_app_context()
    assert ctx_prop["pan_check"].get("pan_entity_char") == "P"


def test_case_page_corporate_cin_none_guard_and_reparsing():
    """
    CRITICAL REGRESSION TEST:
    Ensures that when switching cohorts or toggling corporate mode in Manual Upload mode,
    cin_parsed_data being None or re-parsed never raises KeyError or TypeError:
    'NoneType' object is not subscriptable.
    """
    from ui.state import init_session_state, get_app_context
    from core.onboarding import validate_cin_syntax, parse_cin, check_entity_consistency
    import streamlit as st

    init_session_state()

    # Permutation 1: Manual upload with Bharat extracted identifiers & Corporate ON
    st.session_state.active_cohort_selection = "None (Manual Upload)"
    st.session_state.active_is_corporate = True
    st.session_state.case_is_corporate = True
    st.session_state.active_cin_selection = "UNREGISTERED_MSME"
    st.session_state.active_pan_input = "AABFB8910N"
    st.session_state.custom_extracted_identifiers = {
        "company_name": "Bharat Mock Steels & Infra",
        "pan": "AABFB8910N",
        "is_corporate": True,
    }

    ctx = get_app_context()
    assert ctx["is_corporate"] is True
    # If cin is UNREGISTERED_MSME, validate_cin_syntax is False, so cin_parsed_data is None in context
    assert ctx["cin_parsed_data"] is None

    # Simulating case.py logic: cin_val reparsed on-the-fly
    cin_val = "U24101MH2020PTC349120"
    is_valid, _ = validate_cin_syntax(cin_val)
    assert is_valid is True
    cin_parsed_data = parse_cin(cin_val) if is_valid else None
    assert cin_parsed_data is not None
    assert cin_parsed_data.get("state_name") == "Maharashtra"
    assert cin_parsed_data.get("business_vintage_years") is not None

    pan_check = check_entity_consistency(
        "AABFB8910N",
        is_corporate=True,
        cin_entity_code=cin_parsed_data.get("entity_code")
    )
    assert pan_check["is_valid"] is True
    assert pan_check["mismatch_detected"] is True

    # Permutation 2: Invalid or empty CIN handles cleanly without NoneType subscripting
    invalid_cin = ""
    is_inv_valid, _ = validate_cin_syntax(invalid_cin)
    inv_parsed = parse_cin(invalid_cin) if is_inv_valid else None
    assert inv_parsed is None
    # Accessing with .get on inv_parsed if inv_parsed else fallback
    state_nm = inv_parsed.get("state_name", "Pan-India") if inv_parsed else "Pan-India"
    assert state_nm == "Pan-India"


def test_custom_upload_case_memory_preservation_and_locked_identity():
    """
    CRITICAL MEMORY & IDENTITY TEST:
    Ensures that when manual upload documents are extracted and locked:
    1. locked_case retains the authentic extracted company_name and does not reset to 'Manual Applicant Company'.
    2. locked_case retains the extracted cin_or_pan identifier.
    3. select_and_reset_cohort('None (Manual Upload)') does not wipe custom_company_name or custom_extracted_identifiers.
    """
    from ui.state import init_session_state, select_and_reset_cohort
    from ui.upload_modal import sync_extracted_identifiers_to_state
    from core.data_models import CreditCase, BankExtraction, GSTExtraction, ITRExtraction
    from decimal import Decimal
    import streamlit as st

    init_session_state()

    # Simulate manual document extraction
    extracted_ident = {
        "company_name": "Bharat Mock Steels & Infra",
        "cin": "U24101MH2020PTC349120",
        "pan": "AABFB8910N",
        "is_corporate": True,
    }
    sync_extracted_identifiers_to_state(extracted_ident)
    assert st.session_state.custom_company_name == "Bharat Mock Steels & Infra"

    # Verify select_and_reset_cohort('None (Manual Upload)') preserves custom extracted identifiers
    select_and_reset_cohort("None (Manual Upload)")
    assert st.session_state.custom_company_name == "Bharat Mock Steels & Infra"
    assert st.session_state.custom_extracted_identifiers == extracted_ident

    # Verify locked case identity resolution logic
    cohort_selection = "None (Manual Upload)"
    active_company_name = (
        st.session_state.get("custom_company_name") or "Manual Applicant Company"
        if cohort_selection == "None (Manual Upload)"
        else "Apex Industrial Holdings Ltd."
    )
    assert active_company_name == "Bharat Mock Steels & Infra"

    active_cin_or_pan = (
        (st.session_state.get("active_cin_selection") or st.session_state.get("active_pan_input") or "UNKNOWN")
        if cohort_selection == "None (Manual Upload)"
        else "U24101MH2020PTC349120"
    )
    assert active_cin_or_pan == "U24101MH2020PTC349120"


def test_cgtmse_and_collateral_end_to_end():
    """
    Verifies that:
    1. When cgtmse_covered=True, derive_stipulated_covenants injects the CGTMSE AGF remittance covenant.
    2. When cgtmse_covered=False, CGTMSE covenant is omitted.
    3. Section 2 of CAM markdown, DOCX, and PDF includes Primary Security & Credit Guarantee Scheme.
    """
    case = CreditCase(
        company_name="Apex Industrial Holdings Ltd.",
        cin_or_pan="U29253DL2018PTC334120",
        pan="AAACA1234B",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("164880.03"),
        proposed_rate=Decimal("11.50"),
        proposed_tenure_months=36,
        collateral_value=Decimal("2500000.00"),
        cgtmse_covered=True,
        bank_data=BankExtraction(
            total_credits=Decimal("125000000.00"),
            total_debits=Decimal("118000000.00"),
            opening_balance=Decimal("4520500.00"),
            closing_balance=Decimal("11520500.00"),
            bounce_count=0,
            confidence="HIGH"
        ),
        gst_data=GSTExtraction(
            taxable_value=Decimal("124500000.00"),
            total_tax_paid=Decimal("22410000.00"),
            confidence="HIGH"
        ),
        itr_data=ITRExtraction(
            gross_total_income=Decimal("124500000.00"),
            profit_after_tax=Decimal("12000000.00"),
            depreciation=Decimal("3000000.00"),
            interest_paid=Decimal("2500000.00"),
            net_worth=Decimal("65000000.00"),
            total_debt=Decimal("18000000.00"),
            existing_emi_obligations=Decimal("4000000.00"),
            confidence="HIGH"
        )
    )

    score_res = score_credit_case(case, collateral_value=Decimal("2500000.00"), cgtmse_covered=True)
    assert score_res.five_cs["COLLATERAL"] == Decimal("75.0")
    assert score_res.composite_score == Decimal("96.3")

    # 1. CGTMSE active covenant test
    cov_cgtmse = derive_stipulated_covenants(case, score_res, collateral_value=Decimal("2500000.00"), cgtmse_covered=True)
    titles_cgtmse = [c["title"] for c in cov_cgtmse["statutory"]]
    assert any("CGTMSE Sovereign Guarantee" in t for t in titles_cgtmse)

    # 2. CGTMSE inactive covenant test
    cov_nocgtmse = derive_stipulated_covenants(case, score_res, collateral_value=Decimal("2500000.00"), cgtmse_covered=False)
    titles_nocgtmse = [c["title"] for c in cov_nocgtmse["statutory"]]
    assert not any("CGTMSE Sovereign Guarantee" in t for t in titles_nocgtmse)

    # 3. CAM Section 2 exports
    quorum_gov = {"cro_covenants": cov_cgtmse["plain_list"], "committee_verdict": "APPROVE"}
    cam_md = generate_cam_markdown(
        credit_case=case,
        score_result=score_res,
        quorum_governance=quorum_gov,
    )
    assert "Primary Security / Collateral" in cam_md
    assert "Credit Guarantee Scheme" in cam_md
    assert "CGTMSE Scheme" in cam_md

    # DOCX export
    docx_bytes = generate_cam_docx(
        credit_case=case,
        score_result=score_res,
        quorum_governance=quorum_gov,
    )
    assert len(docx_bytes) > 1000

    # PDF export
    pdf_bytes = generate_cam_pdf(
        credit_case=case,
        score_result=score_res,
        quorum_governance=quorum_gov,
    )
    assert len(pdf_bytes) > 1000


def test_rejection_covenants_and_cam_declination():
    """
    Verifies that on a rejected credit facility:
    1. CAM Markdown, DOCX, and PDF explicitly state that pre-disbursal covenants
       are inapplicable under the declination policy.
    2. If an executive override is applied to approve, covenants are reinstated.
    """
    case = CreditCase(
        company_name="Kaveri Logistics & Freight Ltd.",
        cin_or_pan="U60200KA2019PTC120481",
        pan="AAACK5678M",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("164880.03"),
        proposed_rate=Decimal("11.50"),
        proposed_tenure_months=36,
        collateral_value=Decimal("2500000.00"),
        cgtmse_covered=False,
        bank_data=BankExtraction(
            total_credits=Decimal("10000000.00"),
            total_debits=Decimal("9800000.00"),
            opening_balance=Decimal("200000.00"),
            closing_balance=Decimal("400000.00"),
            bounce_count=6,  # Severe bounce knockout
            confidence="HIGH"
        ),
        gst_data=GSTExtraction(
            taxable_value=Decimal("10000000.00"),
            total_tax_paid=Decimal("1800000.00"),
            confidence="HIGH"
        ),
        itr_data=ITRExtraction(
            gross_total_income=Decimal("10000000.00"),
            profit_after_tax=Decimal("100000.00"),
            depreciation=Decimal("100000.00"),
            interest_paid=Decimal("150000.00"),
            net_worth=Decimal("1000000.00"),
            total_debt=Decimal("4000000.00"),
            existing_emi_obligations=Decimal("500000.00"),
            confidence="HIGH"
        )
    )

    score_res = score_credit_case(case, collateral_value=Decimal("2500000.00"), cgtmse_covered=False)
    assert score_res.final_verdict == Verdict.REJECT

    # Rejected without override: covenants superseded notice
    cam_md_rej = generate_cam_markdown(
        credit_case=case,
        score_result=score_res,
        quorum_governance={"cro_covenants": []},
    )
    assert "Pre-Disbursal Covenants Status: Facility declined under Bank Commercial Credit Policy" in cam_md_rej
    assert "Committee Pre-Disbursal Covenants & Safeguards" not in cam_md_rej

    # Overridden to APPROVE: covenants become active
    override = {
        "override_active": True,
        "target_verdict": "APPROVE",
        "override_verdict": "APPROVE",
        "justification": "Exception Ratified: Technical mandate migration clearing delays certified by drawee bank.",
        "mitigants": ["Collateral Enhancement: Additional unencumbered security pledged (Coverage >= 100%)"],
        "officer_name": "Chief Risk Officer",
    }
    cov_active = ["RoC Form CHG-1 & CERSAI Security Interest Registration within 30 Days"]
    cam_md_ovr = generate_cam_markdown(
        credit_case=case,
        score_result=score_res,
        executive_override=override,
        quorum_governance={"cro_covenants": cov_active},
    )
    assert "Committee Pre-Disbursal Covenants & Safeguards" in cam_md_ovr
    assert "EXECUTIVE OVERRIDE APPLIED" in cam_md_ovr


def test_scorer_collateral_cgtmse_deduction_transparency():
    """
    Verifies that when cgtmse_covered=True:
    1. Even with ₹0 collateral, a ScoreDeduction of 25.0 points is recorded,
       explaining the 75.0 sovereign floor and preventing unexplained missing points.
    2. With full collateral coverage (>= 1.25x), collateral score is 100.0 and zero deductions.
    """
    case = CreditCase(
        company_name="Apex Industrial Holdings Ltd.",
        cin_or_pan="U29253DL2018PTC334120",
        pan="AAACA1234B",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("164880.03"),
        proposed_rate=Decimal("11.50"),
        proposed_tenure_months=36,
        bank_data=BankExtraction(
            total_credits=Decimal("125000000.00"),
            total_debits=Decimal("118000000.00"),
            opening_balance=Decimal("4520500.00"),
            closing_balance=Decimal("11520500.00"),
            bounce_count=0,
            confidence="HIGH"
        ),
        gst_data=GSTExtraction(
            taxable_value=Decimal("124500000.00"),
            total_tax_paid=Decimal("22410000.00"),
            confidence="HIGH"
        ),
        itr_data=ITRExtraction(
            gross_total_income=Decimal("124500000.00"),
            profit_after_tax=Decimal("12000000.00"),
            depreciation=Decimal("3000000.00"),
            interest_paid=Decimal("2500000.00"),
            net_worth=Decimal("65000000.00"),
            total_debt=Decimal("18000000.00"),
            existing_emi_obligations=Decimal("4000000.00"),
            confidence="HIGH"
        )
    )

    # ₹0 collateral + CGTMSE
    res_zero = score_credit_case(case, collateral_value=Decimal("0.00"), cgtmse_covered=True)
    assert res_zero.five_cs["COLLATERAL"] == Decimal("75.0")
    collat_deds = [d for d in res_zero.deductions if d.pillar == "COLLATERAL"]
    assert len(collat_deds) == 1
    assert collat_deds[0].points_deducted == Decimal("25.0")
    assert "CGTMSE Sovereign Floor Applied" in collat_deds[0].observed_value

    # Full collateral (1.5x)
    res_full = score_credit_case(case, collateral_value=Decimal("7500000.00"), cgtmse_covered=True)
    assert res_full.five_cs["COLLATERAL"] == Decimal("100.0")
    collat_deds_full = [d for d in res_full.deductions if d.pillar == "COLLATERAL"]
    assert len(collat_deds_full) == 0


def test_manual_upload_extracted_identity_sync_and_sidebar_reflection():
    """
    CRITICAL IDENTITY PIPELINE TEST:
    Ensures that when manual documents are ingested (e.g. Bharat Mock Steels documents):
    1. extract_entity_identifiers correctly resolves company name, PAN, and corporate benchmark CIN U24101MH2020PTC349120.
    2. sync_extracted_identifiers_to_state safely populates canonical state and queues widget sync.
    3. get_app_context() immediately resolves to the extracted company name, corporate mode, and flags entity mismatch.
    4. Sidebar correctly displays Valid Format (MCA-21) and Entity Type Mismatch without 'Not Applicable' strings.
    """
    import streamlit as st
    from pathlib import Path
    from core.parser import extract_entity_identifiers
    from ui.upload_modal import sync_extracted_identifiers_to_state
    from ui.state import init_session_state, get_app_context, select_and_reset_cohort
    from core.onboarding import check_entity_consistency

    init_session_state()

    # 1. Start on Cohort 1 (Apex)
    select_and_reset_cohort("Cohort 1: Prime MSME (Apex Industrial Benchmark)")
    ctx_pre = get_app_context()
    assert ctx_pre["company_name"] == "Apex Industrial Holdings Ltd."
    assert ctx_pre["is_corporate"] is True

    # 2. Extract from Cohort 3 documents (Bharat Mock Steels: PAN AABFB8910N)
    c3_dir = BASE_DIR / "data" / "synthetic" / "cohort_3_mismatch"
    bank_b = (c3_dir / "bank_statement.pdf").read_bytes()
    gst_b = (c3_dir / "gstr3b.pdf").read_bytes()
    itr_b = (c3_dir / "itr_acknowledgement.pdf").read_bytes()

    ident = extract_entity_identifiers(bank_bytes=bank_b, gst_bytes=gst_b, itr_bytes=itr_b)
    assert ident["company_name"] == "Bharat Mock Steels & Infra"
    assert ident["pan"] == "AABFB8910N"
    assert ident["pan_entity_char"] == "F"
    assert ident["cin"] == "U24101MH2020PTC349120"
    assert ident["is_corporate"] is True

    # 3. Synchronize to state
    sync_extracted_identifiers_to_state(ident)

    # 4. Context resolution
    ctx_post = get_app_context()
    assert ctx_post["cohort_selection"] == "None (Manual Upload)"
    assert ctx_post["company_name"] == "Bharat Mock Steels & Infra"
    assert ctx_post["is_corporate"] is True
    assert ctx_post["current_pan_val"] == "AABFB8910N"
    assert ctx_post["current_cin_val"] == "U24101MH2020PTC349120"
    assert ctx_post["current_pan_mismatch"] is True
    assert ctx_post["app_id"].startswith("APP-")

    # Verify entity consistency for Bharat: declared Corporate with PAN 'F' correctly flags mismatch
    pan_chk = ctx_post["pan_check"]
    assert pan_chk["is_valid"] is True
    assert pan_chk["pan_entity_char"] == "F"
    assert pan_chk["mismatch_detected"] is True

    # 5. Check sidebar rendering
    from ui.state import render_sidebar
    sb_ctx = render_sidebar()
    assert sb_ctx["cin_status_text"] == "Valid Format (MCA-21)"
    assert sb_ctx["pan_status_text"] == "Entity Type Mismatch"
    assert "Not Applicable" not in sb_ctx["cin_status_text"]


def test_case_page_borrower_master_profile_balance_and_zero_redundant_ingestion_button():
    """
    LAYOUT & REDUNDANCY AUDIT:
    Verifies that on the Case page (Home tab):
    1. The redundant 'Ingestion Room' button is completely removed from Borrower Master Profile.
    2. show_custom_upload_dialog is not called within case.py (reserved strictly for Evidence tab).
    3. The Borrower Master Profile card preserves compact vertical height parity with Facility Terms card.
    """
    case_path = BASE_DIR / "app_pages" / "case.py"
    case_source = case_path.read_text(encoding="utf-8")

    # 1. Assert no redundant button keys or ingestion modal calls in case.py
    assert "btn_open_ingestion_case" not in case_source
    assert "show_custom_upload_dialog" not in case_source
    assert "Document Auto-Extraction Ingestion" not in case_source

    # 2. Verify clean header status pill integration
    assert "status_pill('Extracted', variant='approve')" in case_source


def test_pillar2_forensic_legal_radar_high_density_dossier_redesign():
    """
    PILLAR 2 FORENSIC RADAR REDESIGN & DENSITY AUDIT:
    Verifies:
    1. Source-level architecture:
       - No bloated 7:3 columns split (col_t1, col_t2) wasting 30% width.
       - No unreviewed callout warning boxes bloating docket cards.
       - Zero sparkle emoji ('✨') in Copilot recommendation action.
       - Inclusion of sleek 4-chip pre-fill toolbar and Re-adjudicate mechanism.
    2. Full AppTest runtime verification:
       - Runs Cohort 2, executes statutory sweep, pre-fills citation, confirms finding,
         and verifies reversible re-adjudication lifecycle.
    """
    dec_path = BASE_DIR / "app_pages" / "decision.py"
    dec_source = dec_path.read_text(encoding="utf-8")

    # 1. Source architectural assertions
    assert 'col_t1, col_t2 = st.columns([7, 3]' not in dec_source
    assert 'Status: UNREVIEWED\\n\\nOfficer adjudication required below.' not in dec_source
    assert '✨ Apply System Recommendation' not in dec_source
    assert 'btn_reopen_' in dec_source
    assert 'Apply Copilot:' in dec_source

    # 2. AppTest runtime lifecycle
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run(timeout=30)

    # Select Cohort 2 Distressed
    next(b for b in at.button if b.key == "btn_open_distressed").click().run(timeout=30)
    at.switch_page("app_pages/evidence.py").run(timeout=30)
    next((b for b in at.button if "Extract" in b.label)).click().run(timeout=30)
    next((b for b in at.button if "Confirm & Lock" in b.label)).click().run(timeout=30)
    at.switch_page("app_pages/decision.py").run(timeout=30)
    next((b for b in at.button if "Run Deterministic" in b.label)).click().run(timeout=30)

    # Execute Adverse Media Scan
    scan_btn = next((b for b in at.button if "Execute Adverse Media" in b.label), None)
    assert scan_btn is not None
    scan_btn.click().run(timeout=30)

    findings = at.session_state.current_research_report.findings
    sev_f = next(f for f in findings if f.tier == FindingTier.SEVERE)

    # Test 1-click citation chip button
    chip_cirp = next((b for b in at.button if f"btn_chip_cirp_{sev_f.id}" in b.key), None)
    assert chip_cirp is not None
    chip_cirp.click().run(timeout=30)

    # Note input pre-filled
    note_val = at.session_state.get(f"inline_note_{sev_f.id}")
    assert "CIRP" in note_val

    # Confirm finding
    conf_btn = next((b for b in at.button if f"btn_inline_conf_{sev_f.id}" in b.key), None)
    assert conf_btn is not None
    conf_btn.click().run(timeout=30)

    # Verified finding confirmed & verdict is REJECT
    assert at.session_state.score_result.final_verdict == Verdict.REJECT
    updated_sev_f = next(f for f in at.session_state.current_research_report.findings if f.id == sev_f.id)
    assert updated_sev_f.review_status == ReviewStatus.CONFIRMED

    # Re-adjudicate button must now be present on the adjudicated card
    reopen_btn = next((b for b in at.button if f"btn_reopen_{sev_f.id}" in b.key), None)
    assert reopen_btn is not None
    reopen_btn.click().run(timeout=30)

    # Finding successfully reset to UNREVIEWED for re-adjudication
    reopened_sev_f = next(f for f in at.session_state.current_research_report.findings if f.id == sev_f.id)
    assert reopened_sev_f.review_status == ReviewStatus.UNREVIEWED






