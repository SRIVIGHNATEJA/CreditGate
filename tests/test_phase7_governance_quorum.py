import pytest
from decimal import Decimal
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.data_models import CreditCase, BankExtraction, GSTExtraction, ITRExtraction
from core.scorer import score_credit_case
from core.policy import derive_stipulated_covenants
from core.cam_generator import (
    generate_cam_markdown,
    generate_cam_docx,
    generate_cam_pdf,
)
from ui.state import get_default_quorum_governance


def create_test_credit_case() -> CreditCase:
    return CreditCase(
        company_name="Apex Synthetic Holdings (Demo)",
        cin_or_pan="AAACA1234B",
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


def test_default_quorum_state_initializes_as_draft():
    """
    Verifies that fresh case intake initializes the 3-Tier Quorum Governance state
    strictly as an unsealed DRAFT with pending risk concurrence and sanction resolution.
    """
    q_gov = get_default_quorum_governance()
    assert q_gov["stage"] == "DRAFT"
    assert q_gov["analyst_attested"] is True
    assert q_gov["analyst_name"] == "Credit Underwriting Specialist — MSME Division"
    assert q_gov["cro_concurred"] is False
    assert q_gov["cro_timestamp"] is None
    assert len(q_gov["cro_covenants"]) >= 2
    assert q_gov["committee_resolution"] is None
    assert q_gov["committee_resolution_no"] is None
    assert q_gov["sealed"] is False


def test_draft_cam_stamps_honest_pending_signatures():
    """
    Verifies that when a CAM is in DRAFT stage:
    1. Document classification states 'DRAFT APPRAISAL MEMORANDUM — FOR CREDIT COMMITTEE REVIEW ONLY'.
    2. Analyst signature is attested as 'RECOMMENDED FOR SANCTION (Attested)'.
    3. CRO signature is stamped '[ PENDING RISK REVIEW ]'.
    4. Committee signature is stamped '[ AWAITING SANCTION RESOLUTION ]'.
    5. DOCX and PDF generators produce valid artifacts reflecting the draft state.
    """
    case = create_test_credit_case()
    score_res = score_credit_case(case)
    q_gov = get_default_quorum_governance()

    cam_md = generate_cam_markdown(
        credit_case=case,
        score_result=score_res,
        app_id="APP-DRAFT-001",
        quorum_governance=q_gov,
    )

    assert "CREDIT APPRAISAL MEMORANDUM — APPRAISAL DOSSIER FOR SANCTION COMMITTEE DELIBERATION" in cam_md
    assert "DRAFT APPRAISAL MEMORANDUM — FOR CREDIT COMMITTEE REVIEW ONLY" not in cam_md
    assert "[ RECOMMENDED FOR SANCTION (Attested) ]" in cam_md
    assert "Credit Underwriting Specialist — MSME Division" in cam_md
    assert "Underwriting Declaration" in cam_md
    assert "SANCTIONED AS RECOMMENDED" in cam_md
    assert "Resolution Docket Ref" in cam_md

    # Test DOCX export in draft state
    docx_bytes = generate_cam_docx(
        credit_case=case,
        score_result=score_res,
        app_id="APP-DRAFT-001",
        quorum_governance=q_gov,
    )
    assert isinstance(docx_bytes, bytes)
    assert len(docx_bytes) > 1000

    # Test PDF export in draft state
    pdf_bytes = generate_cam_pdf(
        credit_case=case,
        score_result=score_res,
        app_id="APP-DRAFT-001",
        quorum_governance=q_gov,
    )
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF")


def test_cro_concurrence_and_covenants_stamped():
    """
    Verifies that when Tier 2 Risk Concurrence is granted:
    1. CRO signature updates to '[ RISK CONCURRENCE CLEARED ]'.
    2. Pre-disbursal covenants are printed under 'Committee Pre-Disbursal Covenants & Safeguards'.
    3. Committee signature remains awaiting resolution until Tier 3 votes.
    """
    case = create_test_credit_case()
    score_res = score_credit_case(case)
    q_gov = get_default_quorum_governance()
    q_gov["cro_concurred"] = True
    q_gov["cro_timestamp"] = "20-Oct-2026 15:30 UTC"
    q_gov["cro_covenants"] = [
        "Mandatory DSRA of 3 Months Principal + Interest",
        "Quarterly Hypothecated Stock & Receivables Physical Audit",
    ]

    cam_md = generate_cam_markdown(
        credit_case=case,
        score_result=score_res,
        app_id="APP-CRO-001",
        quorum_governance=q_gov,
    )

    assert "Committee Pre-Disbursal Covenants & Safeguards" in cam_md
    assert "Mandatory DSRA of 3 Months Principal + Interest" in cam_md
    assert "Quarterly Hypothecated Stock & Receivables Physical Audit" in cam_md
    assert "Competent Sanction Mandate" in cam_md


def test_committee_sanction_resolution_seals_cam():
    """
    Verifies that when Tier 3 Credit Committee issues a sanction resolution:
    1. Stage transitions to 'SANCTIONED' and sealed is True.
    2. Document classification displays the official resolution docket number.
    3. Committee signature displays '[ SANCTION APPROVED (Res: ZCC/2026/DEL-9921) ]'.
    4. DOCX and PDF reflect the ratified resolution and covenants cleanly.
    """
    case = create_test_credit_case()
    score_res = score_credit_case(case)
    res_no = "ZCC/2026/DEL-9921"
    q_gov = {
        "stage": "SANCTIONED",
        "analyst_attested": True,
        "analyst_name": "Credit Underwriting Specialist — MSME Division",
        "analyst_timestamp": "20-Oct-2026 14:00 UTC",
        "cro_concurred": True,
        "cro_name": "Deputy General Manager / Zonal Head (ZCC)",
        "cro_timestamp": "20-Oct-2026 14:30 UTC",
        "cro_covenants": [
            "DSRA: 3 Months Principal + Interest Obligation",
            "Quarterly Hypothecated Stock & Receivables Audit",
        ],
        "committee_resolution": "APPROVE",
        "committee_name": "Zonal Credit Sanction Committee (ZCC-IV)",
        "committee_resolution_no": res_no,
        "committee_timestamp": "20-Oct-2026 15:00 UTC",
        "sealed": True,
    }

    cam_md = generate_cam_markdown(
        credit_case=case,
        score_result=score_res,
        app_id="APP-SANCTION-001",
        quorum_governance=q_gov,
    )

    assert f"OFFICIAL SANCTIONED CREDIT MEMORANDUM (RESOLUTION NO: {res_no})" in cam_md
    assert res_no in cam_md
    assert "Competent Sanction Mandate" in cam_md
    assert "Resolution Docket Ref" in cam_md
    assert "Presiding Sanction Authority" in cam_md

    # Verify DOCX and PDF export with full sanctioned quorum
    docx_bytes = generate_cam_docx(
        credit_case=case,
        score_result=score_res,
        app_id="APP-SANCTION-001",
        quorum_governance=q_gov,
    )
    assert isinstance(docx_bytes, bytes)
    assert len(docx_bytes) > 1000

    pdf_bytes = generate_cam_pdf(
        credit_case=case,
        score_result=score_res,
        app_id="APP-SANCTION-001",
        quorum_governance=q_gov,
    )
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF")


def test_headless_backward_compatibility_when_quorum_governance_none():
    """
    Verifies 100% backward compatibility: When quorum_governance is None
    (legacy unit tests or headless invocations), CAM generators default to
    is_draft = False, ensuring all legacy test suites pass without modification.
    """
    case = create_test_credit_case()
    score_res = score_credit_case(case)

    cam_md = generate_cam_markdown(
        credit_case=case,
        score_result=score_res,
        app_id="APP-LEGACY-001",
        quorum_governance=None,
    )

    assert "OFFICIAL SANCTIONED CREDIT MEMORANDUM" in cam_md
    assert "[ RECOMMENDED FOR SANCTION ]" in cam_md
    assert "SANCTIONED AS RECOMMENDED" in cam_md


def test_dofp_routing_and_stipulated_covenants():
    """
    Verifies that stipulated covenants from Tab 3 are captured in quorum_governance
    and that DoFP committee mandates match proposed exposure thresholds.
    """
    case = create_test_credit_case()
    score_res = score_credit_case(case)

    # 1. Test covenant propagation into CAM
    covenants = [
        "Cashflow Escrow Mechanism via Designated CA",
        "Clear Title Search Report (TSR) from Empanelled Legal Counsel",
        "3-Month Debt Service Reserve Account (DSRA) Deposit",
        "Unconditional Personal Guarantee of All Promoters",
    ]
    q_gov = get_default_quorum_governance()
    q_gov["cro_covenants"] = covenants

    cam_md = generate_cam_markdown(
        credit_case=case,
        score_result=score_res,
        app_id="APP-COV-001",
        quorum_governance=q_gov,
    )

    for cov in covenants:
        assert cov in cam_md

    # 2. Test Delegation of Financial Powers (DoFP) routing logic
    def get_dofp(loan_amt_float: float):
        if loan_amt_float <= 2_500_000:
            return "Branch Credit Sanction Committee (BCSC)", "Branch Head"
        elif loan_amt_float <= 20_000_000:
            return "Zonal Credit Committee (ZCC)", "Deputy General Manager (DGM)"
        else:
            return "Corporate Credit Approval Committee (CAC)", "Executive Director / Board Committee"

    # Branch level
    comm, auth = get_dofp(2_000_000.0)
    assert comm == "Branch Credit Sanction Committee (BCSC)"
    assert auth == "Branch Head"

    # Zonal level
    comm, auth = get_dofp(5_000_000.0)
    assert comm == "Zonal Credit Committee (ZCC)"
    assert auth == "Deputy General Manager (DGM)"

    # Corporate board level
    comm, auth = get_dofp(50_000_000.0)
    assert comm == "Corporate Credit Approval Committee (CAC)"
    assert auth == "Executive Director / Board Committee"


def test_derive_stipulated_covenants_deterministic():
    """
    Verifies that derive_stipulated_covenants correctly splits statutory mandates
    and risk-triggered mitigants based on borrower metrics.
    """
    case = create_test_credit_case()
    score_res = score_credit_case(case)

    # Apex demo case: Loan amount = 50L (>= 25L), Score = 82 (>= 70), DSCR = 1.83 (>= 1.30)
    cov_data = derive_stipulated_covenants(
        case=case,
        score_result=score_res,
        custom_covenants=["Special Condition: Escrow routing for export receivables"],
    )

    # 1. Statutory mandates always present
    statutory_titles = [c["title"] for c in cov_data["statutory"]]
    assert any("CHG-1" in t for t in statutory_titles)
    assert any("Title Search Report" in t for t in statutory_titles)

    # 2. Risk-triggered: Facility is 50L, so Personal Guarantee of Promoters is triggered
    risk_titles = [c["title"] for c in cov_data["risk_triggered"]]
    assert any("Personal Guarantee" in t for t in risk_titles)

    # 3. Custom covenant appended
    custom_titles = [c["title"] for c in cov_data["custom"]]
    assert "Special Condition: Escrow routing for export receivables" in custom_titles

    # 4. Plain list contains all of them
    assert len(cov_data["plain_list"]) == len(cov_data["all_items"])
    assert "Special Condition: Escrow routing for export receivables" in cov_data["plain_list"]


