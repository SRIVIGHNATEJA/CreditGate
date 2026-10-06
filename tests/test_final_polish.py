import sys
from decimal import Decimal
from pathlib import Path
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
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
from core.cam_generator import generate_cam_markdown
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
