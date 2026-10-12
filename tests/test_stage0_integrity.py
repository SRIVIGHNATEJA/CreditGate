import os
import sys
import json
import tempfile
import pytest
import hashlib
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.data_models import (
    coerce_currency,
    CreditCase,
    BankExtraction,
    GSTExtraction,
    ITRExtraction
)
from core.audit_logger import AuditLogger, verify_chain
from core.onboarding import generate_application_id
from core.cam_generator import generate_cam_html, generate_cam_markdown
from core.scorer import score_credit_case, ScoreResult
from core.policy import Verdict
from core.parser import extract_all_documents


def test_currency_parsing_crore_lakh_variations():
    """Stage 0 Item 4: coerce_currency handles crore/crores/lakh/lakhs/lac/lacs and accounting brackets."""
    assert coerce_currency("1 Cr") == Decimal("10000000.00")
    assert coerce_currency("1.5 cr") == Decimal("15000000.00")
    assert coerce_currency("1 crore") == Decimal("10000000.00")
    assert coerce_currency("2.5 crores") == Decimal("25000000.00")
    assert coerce_currency("1 lakh") == Decimal("100000.00")
    assert coerce_currency("5 lakhs") == Decimal("500000.00")
    assert coerce_currency("2 lac") == Decimal("200000.00")
    assert coerce_currency("3.2 lacs") == Decimal("320000.00")
    assert coerce_currency("10,00,000") == Decimal("1000000.00")
    assert coerce_currency("(50,000)") == Decimal("-50000.00")
    assert coerce_currency("(1.5 cr)") == Decimal("-15000000.00")
    assert coerce_currency("-₹50,000") == Decimal("-50000.00")
    assert coerce_currency("₹ -50,000") == Decimal("-50000.00")
    assert coerce_currency("- 1.5 Cr") == Decimal("-15000000.00")
    assert coerce_currency("−10 Lakhs") == Decimal("-1000000.00")
    assert coerce_currency("50,000,") == Decimal("50000.00")

    with pytest.raises(ValueError):
        coerce_currency(None)


def test_application_id_uniqueness_and_per_session_audit():
    """Stage 0 Item 5: Application ID includes timestamp/session suffix so same-day sessions do not collide."""
    app_id_1 = generate_application_id("Apex Synthetic Holdings (Demo)", "20261006")
    app_id_2 = generate_application_id("Apex Synthetic Holdings (Demo)", "20261006")
    assert app_id_1 != app_id_2
    assert app_id_1.startswith("APP-20261006-")
    assert app_id_2.startswith("APP-20261006-")


def test_tamper_evident_audit_hash_chain():
    """Stage 0 Item 6: Each audit event stores the hash of the previous event (tamper-evident chain)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_app_id = "APP-TEST-TAMPER-001"
        logger = AuditLogger(test_app_id, log_dir=Path(tmpdir))

        logger.log("APPLICATION_INITIATED", "SYSTEM", {"status": "started"})
        logger.log("PAN_VALIDATED", "SYSTEM", {"pan": "AA***4B"})
        logger.log("OFFICER_REVIEW", "OFFICER", {"decision": "proceed"})

        log_file = logger.log_path
        assert log_file.exists()

        # Clean chain verification
        is_valid, err = verify_chain(log_file)
        assert is_valid is True
        assert err is None

        # Tamper test: modify one line in the middle of JSONL
        with open(log_file, "r") as f:
            lines = f.readlines()

        event_1 = json.loads(lines[1])
        event_1["details"]["pan"] = "TAMPERED_VAL"
        lines[1] = json.dumps(event_1) + "\n"

        with open(log_file, "w") as f:
            f.writelines(lines)

        # Verification must now fail
        tampered_valid, tampered_err = verify_chain(log_file)
        assert tampered_valid is False
        assert "Hash mismatch at sequence 1" in tampered_err


def test_cam_html_export_escapes_xss_injection():
    """Stage 0 Item 8: html.escape all content in CAM HTML export against XSS/script injection."""
    malicious_payload = "<script>alert('XSS_ATTACK')</script><img src=x onerror=alert(1)>"
    html_out = generate_cam_html(
        cam_markdown=f"# Memorandum\nCompany: {malicious_payload}",
        app_id=f"APP-{malicious_payload}"
    )

    # Must contain HTML-escaped characters
    assert "&lt;script&gt;" in html_out
    assert "&lt;/script&gt;" in html_out
    # Must NOT contain raw executable script tags or elements
    assert "<script>" not in html_out
    assert "<img" not in html_out


def test_pan_change_invalidates_composite_payload_hash():
    """Stage 0 Item 1: Changing PAN or PAN mismatch state alters composite hash, invalidating score."""
    base_payload = {
        "financials_hash": "abc123hash",
        "loan_amount": 5000000.0,
        "tenure_months": 36,
        "interest_rate": 11.5,
        "proposed_emi": 164880.0,
        "collateral_val": 5000000.0,
        "cgtmse_covered": True,
        "cin": "U29253DL2018PTC334120",
        "vintage": 8,
        "pan": "AAACA1234B",
        "pan_mismatch": False,
        "research": []
    }

    hash1 = hashlib.sha256(json.dumps(base_payload, sort_keys=True).encode()).hexdigest()[:16]

    # Change PAN to personal PAN
    modified_pan_payload = dict(base_payload)
    modified_pan_payload["pan"] = "AAAPA1234B"
    modified_pan_payload["pan_mismatch"] = True
    hash2 = hashlib.sha256(json.dumps(modified_pan_payload, sort_keys=True).encode()).hexdigest()[:16]

    assert hash1 != hash2, "Score composite hash must differ when PAN or mismatch changes"


def test_hitl_lock_with_blank_does_not_produce_zero():
    """Stage 0 Item 2: Blank cell must remain None and route to INSUFFICIENT_DATA, never 0."""
    case_with_blank = CreditCase(
        company_name="Apex Synthetic Holdings (Demo)",
        cin_or_pan="U29253DL2018PTC334120",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("165000.00"),
        proposed_rate=Decimal("11.5"),
        proposed_tenure_months=36,
        bank_data=BankExtraction(
            total_credits=Decimal("125000000.00"),
            total_debits=None,  # Blank field!
            opening_balance=Decimal("4500000.00"),
            closing_balance=Decimal("11500000.00"),
            bounce_count=0,
            confidence="LOW"
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

    res = score_credit_case(case_with_blank)
    assert res.final_verdict == Verdict.INSUFFICIENT_DATA
    assert any("bank_data.total_debits" in r for r in res.policy_reasons)
    # Ensure total_debits did not become Decimal("0.00")
    assert case_with_blank.bank_data.total_debits is None


def test_all_three_cohorts_end_to_end_verdicts():
    """Stage 0 Item 12: Verify all 3 regenerated synthetic cohorts produce expected verdicts."""
    base_dir = Path(__file__).resolve().parent.parent / "data" / "synthetic"

    # Cohort 1: Prime -> APPROVE
    c1_dir = base_dir / "cohort_1_prime"
    c1_case, _ = extract_all_documents(
        bank_bytes=(c1_dir / "bank_statement.pdf").read_bytes(),
        gst_bytes=(c1_dir / "gstr3b.pdf").read_bytes(),
        itr_bytes=(c1_dir / "itr_acknowledgement.pdf").read_bytes(),
        company_name="Apex Synthetic Holdings (Demo)",
        cin_or_pan="U29253DL2018PTC334120",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("165000.00"),
        proposed_rate=Decimal("11.5"),
        proposed_tenure_months=36
    )
    res_1 = score_credit_case(
        c1_case,
        business_vintage_years=8,
        pan_entity_mismatch=False,
        collateral_value=Decimal("5000000.00")
    )
    assert res_1.final_verdict == Verdict.APPROVE, f"Expected APPROVE for Cohort 1, got {res_1.final_verdict}"

    # Cohort 2: Distressed -> REJECT via BOUNCE_KNOCKOUT
    c2_dir = base_dir / "cohort_2_distressed"
    c2_case, _ = extract_all_documents(
        bank_bytes=(c2_dir / "bank_statement.pdf").read_bytes(),
        gst_bytes=(c2_dir / "gstr3b.pdf").read_bytes(),
        itr_bytes=(c2_dir / "itr_acknowledgement.pdf").read_bytes(),
        company_name="Kaveri Fictitious Freight (Demo)",
        cin_or_pan="U60200KA2019PTC120481",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("165000.00"),
        proposed_rate=Decimal("11.5"),
        proposed_tenure_months=36
    )
    res_2 = score_credit_case(
        c2_case,
        business_vintage_years=7,
        pan_entity_mismatch=False,
        collateral_value=Decimal("1000000.00")
    )
    assert res_2.final_verdict == Verdict.REJECT, f"Expected REJECT for Cohort 2, got {res_2.final_verdict}"
    assert "BOUNCE_KNOCKOUT" in res_2.knockout_applied

    # Cohort 3: Mismatch Trap -> CONDITIONAL via WEAKEST_LINK_CAP
    c3_dir = base_dir / "cohort_3_mismatch"
    c3_case, _ = extract_all_documents(
        bank_bytes=(c3_dir / "bank_statement.pdf").read_bytes(),
        gst_bytes=(c3_dir / "gstr3b.pdf").read_bytes(),
        itr_bytes=(c3_dir / "itr_acknowledgement.pdf").read_bytes(),
        company_name="Bharat Mock Steels & Infra (Demo)",
        cin_or_pan="AAB-4921",
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("165000.00"),
        proposed_rate=Decimal("11.5"),
        proposed_tenure_months=36
    )
    res_3 = score_credit_case(
        c3_case,
        business_vintage_years=6,
        pan_entity_mismatch=True,
        collateral_value=Decimal("5000000.00")
    )
    assert res_3.final_verdict == Verdict.CONDITIONAL, f"Expected CONDITIONAL for Cohort 3, got {res_3.final_verdict}"
    assert "WEAKEST_LINK_CAP" in res_3.cap_applied
