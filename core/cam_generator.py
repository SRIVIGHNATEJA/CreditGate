import json
from decimal import Decimal
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from core.policy import Verdict
from core.data_models import CreditCase
from core.scorer import ScoreResult
from core.formatting import format_inr
from core.audit_logger import mask_identifier


class CamGenerationError(Exception):
    """Raised when CAM generation is blocked by policy or data validation."""
    pass


def generate_cam_markdown(
    credit_case: CreditCase,
    score_result: ScoreResult,
    cin_parsed_data: Optional[Dict[str, Any]] = None,
    officer_notes: Optional[List[Dict[str, Any]]] = None,
    executive_override: Optional[Dict[str, Any]] = None,
    audit_events: Optional[List[Dict[str, Any]]] = None,
    app_id: str = "APP-GENERIC-001"
) -> str:
    """
    Renders a bank-grade Credit Appraisal Memorandum (CAM) in structured Markdown.
    Guarantees:
    - Zero raw unmasked PANs (strictly masked for RBI PII compliance).
    - Standard Indian Rupee Lakhs/Crores currency formatting.
    - Full score deduction rule IDs and auditable trail.
    - Blocks CAM generation if Verdict is INSUFFICIENT_DATA.
    """
    if score_result.final_verdict == Verdict.INSUFFICIENT_DATA:
        raise CamGenerationError(
            "CAM_BLOCKED_INSUFFICIENT_DATA: Cannot generate Credit Appraisal Memorandum for an incomplete file. "
            f"Reasons: {'; '.join(score_result.policy_reasons)}"
        )

    b = credit_case.bank_data
    g = credit_case.gst_data
    itr = credit_case.itr_data

    # PII Sanitization
    raw_pan = credit_case.cin_or_pan
    masked_pan = mask_identifier(raw_pan)

    # Determine final effective verdict
    effective_verdict = score_result.final_verdict.name
    verdict_badge = f"**{effective_verdict}**"
    if executive_override and executive_override.get("override_active"):
        veto_verdict = executive_override.get("override_verdict", effective_verdict)
        verdict_badge = f"**{veto_verdict} (EXECUTIVE OVERRIDE APPLIED)**"

    now_str = datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC")

    md = []
    md.append("# CREDIT APPRAISAL MEMORANDUM (CAM)")
    md.append(f"**Application Reference:** `{app_id}` | **Date of Appraisal:** {now_str}  ")
    md.append(f"**Final Underwriting Decision:** {verdict_badge}  ")
    md.append("---")

    # 1. Executive Summary & Borrower Profile
    md.append("## 1. BORROWER ENTITY PROFILE")
    md.append(f"- **Borrower Name:** {credit_case.company_name}")
    md.append(f"- **Permanent Account Number (PAN):** `{masked_pan}` *(Masked under RBI Digital Lending Norms)*")
    
    if cin_parsed_data and cin_parsed_data.get("cin") != "UNREGISTERED_MSME":
        md.append(f"- **Corporate Identification Number (CIN):** `{cin_parsed_data.get('cin')}`")
        md.append(f"- **Entity Classification:** {cin_parsed_data.get('entity_code', 'Private Limited Company')}")
        md.append(f"- **Operating Vintage:** {cin_parsed_data.get('business_vintage_years', 'N/A')} Years")
        md.append(f"- **Industry Macro Sector:** {cin_parsed_data.get('macro_sector', 'Manufacturing')}")
        md.append(f"- **Incorporation Jurisdiction:** {cin_parsed_data.get('state_name', 'India')}")
    else:
        md.append("- **Entity Classification:** Unregistered MSME / Proprietorship")
        md.append(f"- **Operating Vintage:** {cin_parsed_data.get('business_vintage_years', 5) if cin_parsed_data else 5} Years")
        md.append(f"- **Industry Macro Sector:** {cin_parsed_data.get('macro_sector', 'General MSME') if cin_parsed_data else 'General MSME'}")

    md.append("")

    # 2. Facility Proposal & Debt Service
    md.append("## 2. PROPOSED CREDIT FACILITY SPECIFICATIONS")
    md.append("| Facility Parameter | Proposed Terms | Remarks / Standard Banking Benchmark |")
    md.append("|:---|:---|:---|")
    md.append(f"| **Requested Loan Facility** | {format_inr(credit_case.requested_loan_amount)} | Term Loan / Working Capital |")
    md.append(f"| **Indicative Interest Rate** | {credit_case.proposed_rate:.2f}% p.a. | Standard Risk-Adjusted MSME Pricing |")
    md.append(f"| **Tenure** | {credit_case.proposed_tenure_months} Months | Equated Monthly Amortization |")
    md.append(f"| **Calculated Monthly EMI** | {format_inr(score_result.proposed_monthly_emi)} | Annuity Formula Amortization |")
    md.append(f"| **Annual Debt Service Requirement** | {format_inr(score_result.proposed_annual_emi)} | Evaluated in Capacity DSCR |")
    md.append("")

    # 3. Triangulated Financial Analysis
    md.append("## 3. FINANCIAL ANALYSIS & TRIANGULATION")
    md.append("| Financial Metric | Amount (INR) | Source Document | Analytical Observation |")
    md.append("|:---|:---|:---|:---|")
    if itr:
        md.append(f"| Gross Total Income / Revenue | {format_inr(itr.gross_total_income)} | ITR Acknowledgement | Top-line revenue base |")
        md.append(f"| Profit After Tax (PAT) | {format_inr(itr.profit_after_tax)} | Audited P&L Statement | Bottom-line net earnings |")
        md.append(f"| Depreciation & Amortization | {format_inr(itr.depreciation)} | Audited Financials | Non-cash operational addback |")
        md.append(f"| Finance Costs / Interest Paid | {format_inr(itr.interest_paid)} | Audited Financials | Existing operational debt service |")
        md.append(f"| Tangible Net Worth | {format_inr(itr.net_worth)} | Audited Balance Sheet | Capital solvency hurdle |")
        md.append(f"| Total Existing Borrowings | {format_inr(itr.total_debt)} | Audited Balance Sheet | Balance sheet leverage base |")
    if b:
        md.append(f"| Total Bank Inward Credits | {format_inr(b.total_credits)} | Bank Statement (12M) | Cash-basis banking realization |")
        md.append(f"| Total Bank Debits | {format_inr(b.total_debits)} | Bank Statement (12M) | Operational outflow volume |")
        md.append(f"| Inward / Mandate Bounces | {b.bounce_count} Return(s) | Bank Statement (12M) | Mandate discipline check |")
    if g:
        md.append(f"| GST Taxable Turnover | {format_inr(g.taxable_value)} | GSTR-3B Filings | Accrual-basis statutory turnover |")
        md.append(f"| Total GST Tax Paid | {format_inr(g.total_tax_paid)} | GSTR-3B Filings | Statutory compliance proof |")
    md.append("")

    # 4. Solvency & Debt Service Ratios
    md.append("## 4. CORE SOLVENCY & COVERAGE RATIOS")
    md.append(f"- **Operating DSCR:** `{score_result.dscr:.2f}x` *(Benchmark >= 1.20x hurdle, >= 1.50x prime)*")
    md.append("  - *Formula:* `(PAT + Depreciation + Interest Paid) / (Existing EMIs + Proposed EMI)` (Strict single-count interest math).")
    if itr and itr.net_worth and itr.net_worth > Decimal("0"):
        de = (itr.total_debt / itr.net_worth).quantize(Decimal("0.01"))
        md.append(f"- **Gearing / Debt-to-Equity (D/E):** `{de:.2f}x` *(Benchmark <= 1.0x prime, <= 3.0x acceptable)*")
    else:
        md.append("- **Gearing / Debt-to-Equity (D/E):** `CRITICAL` (Negative or zero Net Worth)")
    
    if g and b:
        max_turn = max(g.taxable_value or Decimal("0"), b.total_credits or Decimal("0"))
        if max_turn > Decimal("0"):
            gap_pct = (abs((g.taxable_value or Decimal("0")) - (b.total_credits or Decimal("0"))) / max_turn * Decimal("100")).quantize(Decimal("0.1"))
            md.append(f"- **Accrual vs Cash Turnover Gap:** `{gap_pct}%` *(Benchmark <= 15% normal friction)*")

    md.append("")

    # 5. Deterministic Five Cs Scorecard
    md.append("## 5. DETERMINISTIC FIVE Cs RISK SCORECARD")
    md.append(f"**Composite Score:** `{score_result.composite_score} / 100.0` | **Baseline Verdict:** `{score_result.baseline_verdict.name}`  ")
    md.append("")
    md.append("| Pillar | Weight | Score (/100) | Policy Status |")
    md.append("|:---|:---|:---|:---|")
    for pillar, sc in score_result.five_cs.items():
        status = "✅ Pristine" if sc >= Decimal("70.0") else ("⚠️ Moderate" if sc >= Decimal("40.0") else "🚨 Weakest-Link Cap (< 40)")
        md.append(f"| **{pillar}** | 20% | {sc:.1f} | {status} |")
    md.append("")

    # 6. Auditable Score Deductions Trail
    md.append("## 6. AUDITABLE SCORE DEDUCTION TRAIL")
    if score_result.deductions:
        md.append("| Rule ID | Pillar | Penalty | Observed Value | Threshold | Forensic Explanation |")
        md.append("|:---|:---|:---|:---|:---|:---|")
        for d in score_result.deductions:
            md.append(f"| `{d.rule_id}` | {d.pillar} | -{d.points_deducted:.1f} pts | {d.observed_value} | {d.threshold} | {d.explanation} |")
    else:
        md.append("*No score deductions applied. Applicant scored 100% across all evaluated parameters.*")
    md.append("")

    # 7. Policy Gate Evaluation & Weakest-Link Analysis
    md.append("## 7. POLICY GATE & HARD KNOCKOUT EVALUATION")
    if score_result.cap_applied:
        md.append(f"- **Policy Cap Applied:** `{score_result.cap_applied}`")
    if score_result.knockout_applied:
        md.append(f"- **Hard Knock-Out Triggered:** `{score_result.knockout_applied}`")
    
    md.append("- **Policy Reasons & Deliberations:**")
    for pr in score_result.policy_reasons:
        md.append(f"  - {pr}")
    md.append("")

    # 8. Officer Field Notes & Executive Veto
    md.append("## 8. CREDIT OFFICER FIELD NOTES & EXECUTIVE VETO")
    if officer_notes:
        md.append("### Field Visit Observations")
        for idx, n in enumerate(officer_notes, 1):
            md.append(f"{idx}. **[{n.get('affected_c', 'GENERAL')}]** {n.get('note_text', '')} *(Adjustment: {n.get('adjustment', 0)} pts, Recorded: {n.get('timestamp', '')})*")
    else:
        md.append("*No manual field visit observations appended.*")

    if executive_override and executive_override.get("override_active"):
        md.append("")
        md.append("### ⚠️ Executive Veto Justification (Credit Committee Override)")
        md.append(f"- **Original System Verdict:** `{score_result.final_verdict.name}`")
        md.append(f"- **Executive Override Verdict:** `{executive_override.get('override_verdict', 'N/A')}`")
        md.append(f"- **Overriding Officer / Authority:** `{executive_override.get('officer_name', 'Credit Risk Committee')}`")
        md.append(f"- **Mandatory Written Justification:** \"{executive_override.get('justification_text', '')}\"")
        md.append(f"- **Veto Recorded Timestamp:** `{executive_override.get('timestamp', '')}`")
    md.append("")

    # 9. Forensic Audit Trail Appendix
    md.append("## 9. APPENDIX: FORENSIC AUDIT TRAIL (CHRONOLOGICAL EVENT STREAM)")
    if audit_events:
        md.append("| Timestamp | Event Type | Actor | Severity | Summary Details |")
        md.append("|:---|:---|:---|:---|:---|")
        for evt in audit_events:
            ts = evt.get("timestamp", "")[:19]
            etype = evt.get("event_type", "")
            actor = evt.get("actor", "")
            sev = evt.get("severity", "INFO")
            det_summary = json.dumps(evt.get("details", {}))
            if len(det_summary) > 60:
                det_summary = det_summary[:57] + "..."
            md.append(f"| {ts} | `{etype}` | {actor} | {sev} | {det_summary} |")
    else:
        md.append("*No session audit events logged.*")

    md.append("")
    md.append("---")
    md.append(f"*This Credit Appraisal Memorandum was generated deterministically by CreditGate v3.0 for application `{app_id}`. All rights reserved.*")

    return "\n".join(md)
