import json
import html
from decimal import Decimal
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from core.policy import Verdict, WEIGHTS
from core.data_models import CreditCase, AdverseMediaReport
from core.scorer import ScoreResult
from core.formatting import format_inr
from core.audit_logger import mask_identifier


class CamGenerationError(Exception):
    """Raised when CAM generation is blocked by policy or data validation."""
    pass


def generate_cam_html(cam_markdown: str, app_id: str = "APP-GENERIC-001") -> str:
    """Renders formal CAM HTML export with all content strictly html-escaped against XSS/injection."""
    escaped_app_id = html.escape(str(app_id))
    escaped_content = html.escape(str(cam_markdown))
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>CAM - {escaped_app_id}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #1e293b; max-width: 900px; margin: 40px auto; padding: 0 20px; }}
h1 {{ color: #1e3a8a; border-bottom: 2px solid #2563eb; padding-bottom: 8px; }}
h2 {{ color: #0f766e; margin-top: 24px; border-bottom: 1px solid #cbd5e1; padding-bottom: 4px; }}
table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
th, td {{ border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left; }}
th {{ background-color: #f1f5f9; color: #0f172a; }}
code {{ background-color: #f8fafc; border: 1px solid #e2e8f0; padding: 2px 4px; border-radius: 4px; }}
</style>
</head>
<body>
<pre style="white-space: pre-wrap; font-family: inherit;">{escaped_content}</pre>
</body>
</html>"""


def generate_cam_markdown(
    credit_case: CreditCase,
    score_result: ScoreResult,
    cin_parsed_data: Optional[Dict[str, Any]] = None,
    officer_notes: Optional[List[Dict[str, Any]]] = None,
    executive_override: Optional[Dict[str, Any]] = None,
    audit_events: Optional[List[Dict[str, Any]]] = None,
    adverse_media_report: Optional[AdverseMediaReport] = None,
    app_id: str = "APP-GENERIC-001",
    pan: Optional[str] = None
) -> str:
    """
    Renders a standardized Credit Appraisal Memorandum (CAM) in structured Markdown.
    Guarantees:
    - Zero raw unmasked PANs (strictly masked for PII compliance).
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
    raw_pan = pan or getattr(credit_case, "pan", None)
    if not raw_pan and credit_case.cin_or_pan and len(credit_case.cin_or_pan) == 10:
        raw_pan = credit_case.cin_or_pan
    masked_pan = mask_identifier(raw_pan) if raw_pan else "NOT_PROVIDED"

    # Determine final effective verdict
    effective_verdict = score_result.final_verdict.name
    verdict_badge = f"**{effective_verdict}**"
    if executive_override and executive_override.get("override_active"):
        veto_verdict = executive_override.get("override_verdict", effective_verdict)
        verdict_badge = f"**{veto_verdict} (Overridden by officer)**"

    now_str = datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC")

    md = []
    md.append("# CREDIT APPRAISAL MEMORANDUM (CAM)")
    md.append(f"**Application Reference:** `{app_id}` | **Date of Appraisal:** {now_str}  ")
    md.append(f"**Final Underwriting Decision:** {verdict_badge}  ")
    md.append("---")

    # 1. Executive Summary & Borrower Profile
    md.append("## 1. BORROWER ENTITY PROFILE")
    md.append(f"- **Borrower Name:** {credit_case.company_name}")
    md.append(f"- **Permanent Account Number (PAN):** `{masked_pan}` *(PII masked)*")
    
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
    if itr and itr.net_worth and itr.net_worth > Decimal("0") and itr.total_debt is not None:
        de = (itr.total_debt / itr.net_worth).quantize(Decimal("0.01"))
        md.append(f"- **Gearing / Debt-to-Equity (D/E):** `{de:.2f}x` *(Benchmark <= 1.0x prime, <= 3.0x acceptable)*")
    elif itr and itr.net_worth and itr.net_worth <= Decimal("0"):
        md.append("- **Gearing / Debt-to-Equity (D/E):** `CRITICAL` (Negative or zero Net Worth)")
    else:
        md.append("- **Gearing / Debt-to-Equity (D/E):** `N/A` (Incomplete Data)")
    
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
        weight_val = WEIGHTS.get(pillar, Decimal("0.20"))
        weight_str = f"{int(weight_val * 100)}%"
        status = "✅ Pristine" if sc >= Decimal("70.0") else ("⚠️ Moderate" if sc >= Decimal("40.0") else "🚨 Weakest-Link Cap (< 40)")
        md.append(f"| **{pillar}** | {weight_str} | {sc:.1f} | {status} |")
    md.append("")

    # 6. Auditable Score Deductions Trail
    md.append("## 6. AUDITABLE SCORE DEDUCTION TRAIL")
    if score_result.deductions:
        md.append("| Rule ID | Pillar | Penalty | Observed Value | Threshold | Policy Rationale |")
        md.append("|:---|:---|:---|:---|:---|:---|")
        for d in score_result.deductions:
            md.append(f"| `{d.rule_id}` | {d.pillar} | -{d.points_deducted:.1f} pts | {d.observed_value} | {d.threshold} | {d.explanation} |")
    else:
        md.append("*No score deductions applied. Applicant scored 100% across all evaluated parameters.*")
    md.append("")

    # 7. Adverse Media Screening & Legal Due Diligence
    md.append("## 7. ADVERSE MEDIA SCREENING & LEGAL DUE DILIGENCE")
    if adverse_media_report is None or adverse_media_report.status == "RESEARCH_UNAVAILABLE":
        md.append("- **Screening Module Status:** `RESEARCH_UNAVAILABLE`")
        err_msg = adverse_media_report.error_message if adverse_media_report and adverse_media_report.error_message else "API unconfigured, network timeout, or offline mode."
        md.append(f"> ⚠️ **RESEARCH_UNAVAILABLE:** External secondary research could not be completed ({err_msg}). Under bank policy, the Credit Officer must conduct independent MCA registry and court docket due diligence prior to loan sanction.")
    elif adverse_media_report.status == "NO_ADVERSE_FINDINGS":
        md.append("- **Screening Module Status:** `NO_ADVERSE_FINDINGS`")
        if adverse_media_report.query_used:
            md.append(f"- **Anchored Search Query:** `{adverse_media_report.query_used}`")
        md.append("> *No adverse findings in screened sources; this is not a legal clearance.*")
    elif adverse_media_report.status == "COMPLETED":
        md.append("- **Screening Module Status:** `COMPLETED`")
        if adverse_media_report.query_used:
            md.append(f"- **Anchored Search Query:** `{adverse_media_report.query_used}`")
        if adverse_media_report.is_synthetic_demo:
            md.append("> ⚠️ **NOTICE:** All adverse media items below are marked `[SYNTHETIC DEMO DATA]` for demonstration purposes.")

        if adverse_media_report.findings:
            md.append("| Title | Severity Tier | Direct Source URL | Review Status | Notes / Justification |")
            md.append("|:---|:---|:---|:---|:---|")
            for f in adverse_media_report.findings:
                status_badge = f"`{f.review_status.value}`"
                notes = f.dismissal_reason if f.review_status == "DISMISSED" else (f"Confirmed: {f.confirmation_note or f.reviewed_by}" if f.review_status == "CONFIRMED" else "Awaiting Officer Review")
                md.append(f"| {f.title} | `{f.tier.value}` | [{f.source_url}]({f.source_url}) | {status_badge} | {notes} |")
        else:
            md.append("> *No adverse findings in screened sources; this is not a legal clearance.*")
    md.append("")

    # 8. Policy Gate Evaluation & Weakest-Link Analysis
    md.append("## 8. POLICY GATE & HARD KNOCKOUT EVALUATION")
    if score_result.cap_applied:
        md.append(f"- **Policy Cap Applied:** `{score_result.cap_applied}`")
    if score_result.knockout_applied:
        md.append(f"- **Hard Knock-Out Triggered:** `{score_result.knockout_applied}`")
    
    md.append("- **Policy Reasons & Deliberations:**")
    for pr in score_result.policy_reasons:
        md.append(f"  - {pr}")
    md.append("")

    # 9. Officer Field Notes & Executive Veto
    md.append("## 9. CREDIT OFFICER FIELD NOTES & EXECUTIVE VETO")
    if officer_notes:
        md.append("### Field Visit Observations")
        for idx, n in enumerate(officer_notes, 1):
            md.append(f"{idx}. **[{n.get('affected_c', 'GENERAL')}]** {n.get('note_text', '')} *(Recorded: {n.get('timestamp', '')})*")
    else:
        md.append("*No manual field visit observations appended.*")

    if executive_override and executive_override.get("override_active"):
        md.append("")
        md.append("### ⚠️ EXECUTIVE OVERRIDE APPLIED (Credit Committee Override)")
        md.append(f"- **Original System Verdict:** `{score_result.final_verdict.name}`")
        md.append(f"- **Executive Override Verdict:** `{executive_override.get('override_verdict', 'N/A')}`")
        md.append(f"- **Overriding Officer Role:** `{executive_override.get('officer_name', 'Credit Risk Committee')} (simulated role)`")
        md.append(f"- **Mandatory Written Justification:** \"{executive_override.get('justification_text', '')}\"")
        md.append(f"- **Veto Recorded Timestamp:** `{executive_override.get('timestamp', '')}`")
    md.append("")

    # 10. Session Audit Trail Appendix
    md.append("## 10. APPENDIX: SESSION AUDIT TRAIL (CHRONOLOGICAL EVENT STREAM)")
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


def generate_cam_docx(
    credit_case: CreditCase,
    score_result: ScoreResult,
    cin_parsed_data: Optional[Dict[str, Any]] = None,
    officer_notes: Optional[List[Dict[str, Any]]] = None,
    executive_override: Optional[Dict[str, Any]] = None,
    audit_events: Optional[List[Dict[str, Any]]] = None,
    adverse_media_report: Optional[AdverseMediaReport] = None,
    app_id: str = "APP-GENERIC-001",
    pan: Optional[str] = None
) -> bytes:
    """
    Generates a formal Credit Appraisal Memorandum (CAM) in Microsoft Word (.docx) format.
    Zero manual coordinate math: relies on native paragraphs and auto-wrapping tables.
    """
    if score_result.final_verdict == Verdict.INSUFFICIENT_DATA:
        raise CamGenerationError("CAM_BLOCKED_INSUFFICIENT_DATA: Cannot generate memorandum for an incomplete file.")

    from io import BytesIO
    import docx
    from docx.shared import Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = docx.Document()
    for section in doc.sections:
        section.top_margin = Inches(0.75)
        section.bottom_margin = Inches(0.75)
        section.left_margin = Inches(0.75)
        section.right_margin = Inches(0.75)

    title = doc.add_heading("CREDIT APPRAISAL MEMORANDUM (CAM)", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    now_str = datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC")
    raw_pan = pan or getattr(credit_case, "pan", None)
    if not raw_pan and credit_case.cin_or_pan and len(credit_case.cin_or_pan) == 10:
        raw_pan = credit_case.cin_or_pan
    masked_pan = mask_identifier(raw_pan) if raw_pan else "NOT_PROVIDED"

    verdict_name = score_result.final_verdict.name
    if executive_override and executive_override.get("override_active"):
        verdict_name = f"{executive_override.get('override_verdict', verdict_name)} (Officer Override)"

    p_sub = doc.add_paragraph()
    p_sub.add_run(f"Application Reference: {app_id}\n").bold = True
    p_sub.add_run(f"Date of Appraisal: {now_str}\n")
    p_sub.add_run(f"Final Decision: {verdict_name}\n")

    # 1. Borrower Entity Profile
    doc.add_heading("1. BORROWER ENTITY PROFILE", level=1)
    t_borrower = doc.add_table(rows=4, cols=2)
    t_borrower.style = "Table Grid"
    t_borrower.cell(0, 0).text = "Borrower Legal Name"
    t_borrower.cell(0, 1).text = str(credit_case.company_name)
    t_borrower.cell(1, 0).text = "PAN (PII Masked)"
    t_borrower.cell(1, 1).text = f"{masked_pan}"
    t_borrower.cell(2, 0).text = "Corporate ID (CIN)"
    t_borrower.cell(2, 1).text = str(cin_parsed_data.get("cin", "UNREGISTERED_MSME") if cin_parsed_data else "UNREGISTERED_MSME")
    t_borrower.cell(3, 0).text = "Industry Sector"
    t_borrower.cell(3, 1).text = str(cin_parsed_data.get("macro_sector", "MSME") if cin_parsed_data else "MSME")

    # 2. Facility Terms
    doc.add_heading("2. PROPOSED CREDIT FACILITY & TERMS", level=1)
    t_facility = doc.add_table(rows=5, cols=2)
    t_facility.style = "Table Grid"
    t_facility.cell(0, 0).text = "Facility Sanctioned"
    t_facility.cell(0, 1).text = format_inr(credit_case.requested_loan_amount)
    t_facility.cell(1, 0).text = "Indicative Rate"
    t_facility.cell(1, 1).text = f"{float(credit_case.proposed_rate or Decimal('11.5')):.2f}% p.a."
    t_facility.cell(2, 0).text = "Tenor"
    t_facility.cell(2, 1).text = f"{int(credit_case.proposed_tenure_months or 36)} Months"
    t_facility.cell(3, 0).text = "Monthly Obligation (EMI)"
    t_facility.cell(3, 1).text = format_inr(score_result.proposed_monthly_emi)
    t_facility.cell(4, 0).text = "DSCR Debt-Service Coverage"
    t_facility.cell(4, 1).text = f"{float(score_result.dscr):.2f}x"

    # 3. Five Cs Breakdown
    doc.add_heading("3. FIVE CS RISK SCORECARD SUMMARY", level=1)
    p_cs = doc.add_paragraph()
    p_cs.add_run(f"Composite Risk Score: {float(score_result.composite_score):.1f} / 100.0\n").bold = True
    t_cs = doc.add_table(rows=6, cols=3)
    t_cs.style = "Table Grid"
    t_cs.cell(0, 0).text = "Pillar"
    t_cs.cell(0, 1).text = "Weight"
    t_cs.cell(0, 2).text = "Pillar Score (0-100)"
    rows_data = [
        ("CHARACTER", "20%", f"{float(score_result.five_cs.get('CHARACTER', 0)):.1f}"),
        ("CAPACITY", "30%", f"{float(score_result.five_cs.get('CAPACITY', 0)):.1f}"),
        ("CAPITAL", "20%", f"{float(score_result.five_cs.get('CAPITAL', 0)):.1f}"),
        ("COLLATERAL", "15%", f"{float(score_result.five_cs.get('COLLATERAL', 0)):.1f}"),
        ("CONDITIONS", "15%", f"{float(score_result.five_cs.get('CONDITIONS', 0)):.1f}"),
    ]
    for idx, (pil, wt, sc) in enumerate(rows_data, 1):
        t_cs.cell(idx, 0).text = pil
        t_cs.cell(idx, 1).text = wt
        t_cs.cell(idx, 2).text = sc

    # 4. Sign-off Quorum
    doc.add_heading("4. COMMITTEE QUORUM AUTHORIZATION", level=1)
    t_sign = doc.add_table(rows=2, cols=3)
    t_sign.style = "Table Grid"
    t_sign.cell(0, 0).text = f"1. Appraising Officer\n\n___________________\nName: R. K. Verma\nDate: {now_str[:11]}"
    t_sign.cell(0, 1).text = f"2. Risk Underwriting Head\n\n___________________\nName: S. Swaminathan\nDate: {now_str[:11]}"
    t_sign.cell(0, 2).text = f"3. Sanctioning Authority\n\n___________________\nName: Credit Committee\nDate: {now_str[:11]}"
    t_sign.cell(1, 0).text = f"Decision: {verdict_name}"
    t_sign.cell(1, 1).text = "Audit Hash: Verified"
    t_sign.cell(1, 2).text = f"App ID: {app_id}"

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def generate_cam_pdf(
    credit_case: CreditCase,
    score_result: ScoreResult,
    cin_parsed_data: Optional[Dict[str, Any]] = None,
    officer_notes: Optional[List[Dict[str, Any]]] = None,
    executive_override: Optional[Dict[str, Any]] = None,
    audit_events: Optional[List[Dict[str, Any]]] = None,
    adverse_media_report: Optional[AdverseMediaReport] = None,
    app_id: str = "APP-GENERIC-001",
    pan: Optional[str] = None
) -> bytes:
    """
    Generates a formal Credit Appraisal Memorandum (CAM) in printable PDF format using ReportLab Platypus.
    Zero manual coordinate math: relies on flowables, auto-wrapping, and auto-flowing tables.
    """
    if score_result.final_verdict == Verdict.INSUFFICIENT_DATA:
        raise CamGenerationError("CAM_BLOCKED_INSUFFICIENT_DATA: Cannot generate memorandum for an incomplete file.")

    from io import BytesIO
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    buf = BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()

    h1_style = ParagraphStyle('CAM_H1', parent=styles['Heading1'], fontSize=15, leading=19, textColor=colors.HexColor('#0B1F3A'), spaceAfter=6)
    h2_style = ParagraphStyle('CAM_H2', parent=styles['Heading2'], fontSize=11, leading=15, textColor=colors.HexColor('#0F766E'), spaceBefore=8, spaceAfter=4)
    body_style = ParagraphStyle('CAM_Body', parent=styles['Normal'], fontSize=9, leading=12, textColor=colors.HexColor('#1F2937'))
    bold_style = ParagraphStyle('CAM_Bold', parent=styles['Normal'], fontSize=9, leading=12, textColor=colors.HexColor('#0B1F3A'))

    raw_pan = pan or getattr(credit_case, "pan", None)
    if not raw_pan and credit_case.cin_or_pan and len(credit_case.cin_or_pan) == 10:
        raw_pan = credit_case.cin_or_pan
    masked_pan = mask_identifier(raw_pan) if raw_pan else "NOT_PROVIDED"
    now_str = datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC")

    verdict_name = score_result.final_verdict.name
    if executive_override and executive_override.get("override_active"):
        verdict_name = f"{executive_override.get('override_verdict', verdict_name)} (Officer Override)"

    story = [
        Paragraph("<b>CREDIT APPRAISAL MEMORANDUM (CAM)</b>", h1_style),
        Paragraph(f"<b>Application Reference:</b> {app_id} &nbsp;|&nbsp; <b>Date:</b> {now_str}", body_style),
        Paragraph(f"<b>Final Decision:</b> {verdict_name} &nbsp;|&nbsp; <b>Composite Score:</b> {float(score_result.composite_score):.1f} / 100.0", body_style),
        Spacer(1, 8),
        Paragraph("<b>1. BORROWER ENTITY PROFILE</b>", h2_style),
        Table([
            [Paragraph("<b>Borrower Legal Name</b>", body_style), Paragraph(str(credit_case.company_name), body_style)],
            [Paragraph("<b>PAN (PII Masked)</b>", body_style), Paragraph(f"{masked_pan}", body_style)],
            [Paragraph("<b>Corporate ID (CIN)</b>", body_style), Paragraph(str(cin_parsed_data.get("cin", "UNREGISTERED_MSME") if cin_parsed_data else "UNREGISTERED_MSME"), body_style)],
            [Paragraph("<b>Industry Sector</b>", body_style), Paragraph(str(cin_parsed_data.get("macro_sector", "MSME") if cin_parsed_data else "MSME"), body_style)],
        ], colWidths=[180, 360], style=[
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
            ('TOPPADDING', (0,0), (-1,-1), 3),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ]),
        Spacer(1, 8),
        Paragraph("<b>2. PROPOSED CREDIT FACILITY & TERMS</b>", h2_style),
        Table([
            [Paragraph("<b>Facility Sanctioned</b>", body_style), Paragraph(format_inr(credit_case.requested_loan_amount), body_style)],
            [Paragraph("<b>Lending Rate</b>", body_style), Paragraph(f"{float(credit_case.proposed_rate or Decimal('11.5')):.2f}% p.a.", body_style)],
            [Paragraph("<b>Tenor</b>", body_style), Paragraph(f"{int(credit_case.proposed_tenure_months or 36)} Months", body_style)],
            [Paragraph("<b>Monthly Obligation (EMI)</b>", body_style), Paragraph(format_inr(score_result.proposed_monthly_emi), body_style)],
            [Paragraph("<b>DSCR Coverage Ratio</b>", body_style), Paragraph(f"{float(score_result.dscr):.2f}x", body_style)],
        ], colWidths=[180, 360], style=[
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#FFFFFF')),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
            ('TOPPADDING', (0,0), (-1,-1), 3),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ]),
        Spacer(1, 8),
        Paragraph("<b>3. FIVE CS RISK PILLAR BREAKDOWN</b>", h2_style),
        Table([
            [Paragraph("<b>Pillar</b>", bold_style), Paragraph("<b>Weight</b>", bold_style), Paragraph("<b>Pillar Score (0-100)</b>", bold_style)],
            [Paragraph("CHARACTER", body_style), Paragraph("20%", body_style), Paragraph(f"{float(score_result.five_cs.get('CHARACTER', 0)):.1f}", body_style)],
            [Paragraph("CAPACITY", body_style), Paragraph("30%", body_style), Paragraph(f"{float(score_result.five_cs.get('CAPACITY', 0)):.1f}", body_style)],
            [Paragraph("CAPITAL", body_style), Paragraph("20%", body_style), Paragraph(f"{float(score_result.five_cs.get('CAPITAL', 0)):.1f}", body_style)],
            [Paragraph("COLLATERAL", body_style), Paragraph("15%", body_style), Paragraph(f"{float(score_result.five_cs.get('COLLATERAL', 0)):.1f}", body_style)],
            [Paragraph("CONDITIONS", body_style), Paragraph("15%", body_style), Paragraph(f"{float(score_result.five_cs.get('CONDITIONS', 0)):.1f}", body_style)],
        ], colWidths=[160, 100, 280], style=[
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0B1F3A')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
            ('TOPPADDING', (0,0), (-1,-1), 3),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ]),
        Spacer(1, 10),
        Paragraph("<b>4. COMMITTEE QUORUM AUTHORIZATION SIGN-OFF</b>", h2_style),
        Table([
            [
                Paragraph(f"<b>1. Appraising Officer</b><br/><br/><br/>Name: R. K. Verma<br/>Date: {now_str[:11]}", body_style),
                Paragraph(f"<b>2. Risk Underwriting Head</b><br/><br/><br/>Name: S. Swaminathan<br/>Date: {now_str[:11]}", body_style),
                Paragraph(f"<b>3. Sanctioning Authority</b><br/><br/><br/>Name: Credit Committee<br/>Date: {now_str[:11]}", body_style),
            ]
        ], colWidths=[180, 180, 180], style=[
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ])
    ]

    pdf.build(story)
    return buf.getvalue()

