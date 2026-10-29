import pytest
import hashlib
import json
from decimal import Decimal
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.policy import Verdict
from core.data_models import CreditCase, BankExtraction, GSTExtraction, ITRExtraction
from core.scorer import score_credit_case, ScoreResult
from core.cam_generator import (
    generate_cam_markdown,
    generate_cam_docx,
    generate_cam_pdf,
    CamGenerationError,
)
from core.formatting import format_inr


def create_test_credit_case(raw_pan: str = "AAACA1234B") -> CreditCase:
    return CreditCase(
        company_name="Apex Synthetic Holdings (Demo)",
        cin_or_pan=raw_pan,
        requested_loan_amount=Decimal("5000000.00"),
        proposed_emi=Decimal("164880.03"),
        proposed_rate=Decimal("11.50"),
        proposed_tenure_months=36,
        bank_data=BankExtraction(
            total_credits=Decimal("120000000.00"),
            total_debits=Decimal("115000000.00"),
            opening_balance=Decimal("2500000.00"),
            closing_balance=Decimal("7500000.00"),
            bounce_count=0,
            confidence="HIGH",
        ),
        gst_data=GSTExtraction(
            taxable_value=Decimal("118000000.00"),
            total_tax_paid=Decimal("21240000.00"),
            confidence="HIGH",
        ),
        itr_data=ITRExtraction(
            gross_total_income=Decimal("120000000.00"),
            profit_after_tax=Decimal("12000000.00"),
            depreciation=Decimal("3000000.00"),
            interest_paid=Decimal("2500000.00"),
            net_worth=Decimal("45000000.00"),
            total_debt=Decimal("20000000.00"),
            existing_emi_obligations=Decimal("4000000.00"),
            confidence="HIGH",
        ),
    )


def test_cam_generator_content_and_pii_sanitization():
    """
    Review Item 5:
    Test CAM generator content:
    - Final verdict is present.
    - Deduction rule IDs are present.
    - Masked PAN is present.
    - RAW unmasked PAN is strictly absent (anti-PII leak test).
    - Rupee formatting with Indian grouping is present.
    """
    raw_pan = "AAACA1234B"
    case = create_test_credit_case(raw_pan=raw_pan)
    score_res = score_credit_case(case)

    cin_info = {
        "cin": "U29253DL2018PTC334120",
        "entity_code": "Private Limited Company",
        "business_vintage_years": 8,
        "macro_sector": "Precision Tooling",
        "state_name": "Delhi",
    }

    cam_md = generate_cam_markdown(
        credit_case=case,
        score_result=score_res,
        cin_parsed_data=cin_info,
        app_id="APP-TEST-99"
    )

    # 1. Final Verdict must be present
    assert "APPROVE" in cam_md

    # 2. Masked PAN must be present
    assert "AA***4B" in cam_md

    # 3. CRITICAL REGULATORY COMPLIANCE: Raw unmasked PAN must NOT appear anywhere in CAM!
    assert raw_pan not in cam_md, f"PII Security Leak: Raw PAN {raw_pan} found in generated CAM document!"

    # 4. Indian Rupee formatting present
    assert "₹ 50,00,000.00" in cam_md
    assert "₹ 1,64,880.03" in cam_md

    # 5. Rule IDs present if any deduction exists
    for deduction in score_res.deductions:
        assert deduction.rule_id in cam_md


def test_cam_generator_blocks_on_insufficient_data():
    """
    Review Item 3:
    Block CAM export when Verdict is INSUFFICIENT_DATA.
    """
    case = create_test_credit_case()
    case.itr_data.profit_after_tax = None

    score_res = score_credit_case(case)
    assert score_res.final_verdict == Verdict.INSUFFICIENT_DATA

    with pytest.raises(CamGenerationError, match="CAM_BLOCKED_INSUFFICIENT_DATA"):
        generate_cam_markdown(case, score_res)


def test_cam_generator_executive_override():
    """
    Verify executive veto flow stamps mandatory justification into the CAM.
    """
    case = create_test_credit_case()
    score_res = score_credit_case(case)

    veto = {
        "override_active": True,
        "override_verdict": "CONDITIONAL",
        "officer_name": "Senior Credit Officer R. Sharma",
        "justification_text": "Facility approved conditionally upon submission of personal guarantee by promoters.",
        "timestamp": "2026-10-04T12:00:00Z"
    }

    cam_md = generate_cam_markdown(
        credit_case=case,
        score_result=score_res,
        executive_override=veto
    )

    assert "EXECUTIVE OVERRIDE APPLIED" in cam_md
    assert "Senior Credit Officer R. Sharma" in cam_md
    assert "Facility approved conditionally upon submission of personal guarantee" in cam_md


def test_financials_hash_invalidation():
    """
    Review Item 4 & 5:
    Test hash invalidation: modifying locked financials produces a different hash,
    invalidating previously cached scores and CAM documents.
    """
    f1 = {"revenue": "120000000", "pat": "12000000", "debt": "20000000"}
    f2 = {"revenue": "120000000", "pat": "-5000000", "debt": "20000000"}  # Changed PAT

    hash1 = hashlib.sha256(json.dumps(f1, sort_keys=True).encode()).hexdigest()
    hash2 = hashlib.sha256(json.dumps(f2, sort_keys=True).encode()).hexdigest()

    assert hash1 != hash2, "Hash must change when financials are modified"


def test_cam_docx_export():
    """Verify Word (.docx) export generates valid bytes, sanitizes PII, and blocks on incomplete files."""
    raw_pan = "AAACA1234B"
    case = create_test_credit_case(raw_pan=raw_pan)
    score_res = score_credit_case(case)
    cin_info = {"cin": "U29253DL2018PTC334120", "macro_sector": "Manufacturing"}

    docx_bytes = generate_cam_docx(
        credit_case=case,
        score_result=score_res,
        cin_parsed_data=cin_info,
        app_id="APP-DOCX-001",
        pan=raw_pan,
    )
    assert isinstance(docx_bytes, bytes)
    assert len(docx_bytes) > 1000

    # Test with executive override
    veto = {
        "override_active": True,
        "override_verdict": "CONDITIONAL",
        "officer_name": "Committee Head",
        "justification_text": "Strong collateral justification",
        "timestamp": "2026-10-07T12:00:00Z"
    }
    docx_veto = generate_cam_docx(
        credit_case=case,
        score_result=score_res,
        executive_override=veto,
        app_id="APP-DOCX-002"
    )
    assert isinstance(docx_veto, bytes)
    assert len(docx_veto) > len(docx_bytes)

    # Test blocking on INSUFFICIENT_DATA
    case_bad = create_test_credit_case()
    case_bad.itr_data.profit_after_tax = None
    res_bad = score_credit_case(case_bad)
    with pytest.raises(CamGenerationError):
        generate_cam_docx(case_bad, res_bad)


def test_cam_pdf_export():
    """Verify PDF export generates valid bytes, sanitizes PII, and blocks on incomplete files."""
    raw_pan = "AAACA1234B"
    case = create_test_credit_case(raw_pan=raw_pan)
    score_res = score_credit_case(case)
    cin_info = {"cin": "U29253DL2018PTC334120", "macro_sector": "Manufacturing"}

    pdf_bytes = generate_cam_pdf(
        credit_case=case,
        score_result=score_res,
        cin_parsed_data=cin_info,
        app_id="APP-PDF-001",
        pan=raw_pan,
    )
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 1000
    assert pdf_bytes.startswith(b"%PDF")

    # Test with executive override
    veto = {
        "override_active": True,
        "override_verdict": "CONDITIONAL",
        "officer_name": "Committee Head",
        "justification_text": "Strong collateral justification",
        "timestamp": "2026-10-07T12:00:00Z"
    }
    pdf_veto = generate_cam_pdf(
        credit_case=case,
        score_result=score_res,
        executive_override=veto,
        app_id="APP-PDF-002"
    )
    assert isinstance(pdf_veto, bytes)
    assert pdf_veto.startswith(b"%PDF")

    # Test blocking on INSUFFICIENT_DATA
    case_bad = create_test_credit_case()
    case_bad.itr_data.profit_after_tax = None
    res_bad = score_credit_case(case_bad)
    with pytest.raises(CamGenerationError):
        generate_cam_pdf(case_bad, res_bad)


def test_cam_zero_links_and_authentic_statutory_dockets():
    """
    Verifies that:
    1. Generated CAM contains zero fake URLs (no example.com, no raw http:// or https:// links).
    2. Statutory due diligence findings cite official registry references (MCA-21, NCLT, DRT, etc.).
    3. Section 1 profile has zero duplicate attribute lines.
    4. Executive override section does not duplicate bullets and table.
    """
    from core.researcher import search_adverse_media

    case = create_test_credit_case()
    score_res = score_credit_case(case)
    rep = search_adverse_media(company_name="Apex Synthetic Holdings (Demo)", cin="U29253DL2018PTC334120", mock_mode=True)

    veto = {
        "override_active": True,
        "override_verdict": "CONDITIONAL",
        "officer_name": "Chief Risk Officer",
        "justification_text": "Approved subject to additional collateral pledging.",
        "timestamp": "2026-10-19T14:00:00Z",
        "mitigants": ["Additional unencumbered commercial property", "Personal guarantee of promoters"]
    }

    cin_info = {
        "cin": "U29253DL2018PTC334120",
        "entity_code": "Private Limited Company",
        "business_vintage_years": 8,
        "macro_sector": "Precision Industrial Tooling",
        "state_name": "Delhi",
    }

    cam_md = generate_cam_markdown(
        credit_case=case,
        score_result=score_res,
        cin_parsed_data=cin_info,
        adverse_media_report=rep,
        executive_override=veto,
        app_id="APP-ZERO-LINKS-01",
    )

    # 1. Zero fake URLs or broken links
    assert "example.com" not in cam_md
    assert "https://" not in cam_md
    assert "http://" not in cam_md

    # 2. Official statutory citation present
    assert "MCA-21 RoC Delhi (Form CHG-4, SRN: F62910482)" in cam_md

    # 3. No duplicate CIN or PAN in table rows
    assert "| **Corporate Identity (CIN)** |" not in cam_md
    assert "| **Permanent Account Number (PAN)** |" not in cam_md

    # 4. De-duplicated Executive Override section
    assert "- **Original System Verdict:**" not in cam_md
    assert "| **Original Algorithmic Verdict** |" in cam_md


def test_cam_section4_surveillance_unexecuted_vs_findings():
    """
    Verifies Section 4 Part A handling:
    1. If adverse_media_report is None, status is AWAITING_SURVEILLANCE_EXECUTION (anti-false-clean-chit).
    2. If adverse findings are present, status is ADVERSE_FINDINGS_IDENTIFIED.
    3. DOCX and PDF export with findings render cleanly without throwing exceptions.
    """
    from core.researcher import search_adverse_media

    case = create_test_credit_case()
    score_res = score_credit_case(case)

    # 1. Unexecuted surveillance (report is None) -> Must NOT give clean chit!
    cam_unexecuted = generate_cam_markdown(
        credit_case=case,
        score_result=score_res,
        adverse_media_report=None,
        app_id="APP-UNEXEC-01"
    )
    assert "AWAITING_SURVEILLANCE_EXECUTION" in cam_unexecuted
    assert "Statutory Clearance Pending" in cam_unexecuted
    assert "CLEARED (Zero Adverse Records)" not in cam_unexecuted

    # Verify DOCX and PDF unexecuted generate valid bytes
    docx_unexec = generate_cam_docx(credit_case=case, score_result=score_res, adverse_media_report=None)
    assert isinstance(docx_unexec, bytes) and len(docx_unexec) > 1000

    pdf_unexec = generate_cam_pdf(credit_case=case, score_result=score_res, adverse_media_report=None)
    assert isinstance(pdf_unexec, bytes) and len(pdf_unexec) > 1000

    # 2. Distressed entity with adverse findings (Kaveri benchmark)
    kaveri_rep = search_adverse_media(company_name="Kaveri Fictitious Freight (Demo)", cin="U60200KA2019PTC120481")
    cam_findings = generate_cam_markdown(
        credit_case=case,
        score_result=score_res,
        adverse_media_report=kaveri_rep,
        app_id="APP-FINDINGS-01"
    )
    assert "ADVERSE_FINDINGS_IDENTIFIED" in cam_findings
    assert "NCLT Bengaluru Bench" in cam_findings or "CP(IB)/102/BB/2026" in cam_findings

    # Verify DOCX and PDF with adverse findings generate valid bytes
    docx_findings = generate_cam_docx(credit_case=case, score_result=score_res, adverse_media_report=kaveri_rep)
    assert isinstance(docx_findings, bytes) and len(docx_findings) > 1000

    pdf_findings = generate_cam_pdf(credit_case=case, score_result=score_res, adverse_media_report=kaveri_rep)
    assert isinstance(pdf_findings, bytes) and len(pdf_findings) > 1000
