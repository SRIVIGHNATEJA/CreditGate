import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
import logging
import requests

from core.data_models import (
    ReviewStatus,
    FindingTier,
    AdverseMediaFinding,
    AdverseMediaReport,
)

logger = logging.getLogger("creditgate.researcher")

# ============================================================================
# 1. DISAMBIGUATED QUERY BUILDER
# ============================================================================

def build_search_query(
    company_name: str,
    cin: Optional[str] = None,
    promoter_name: Optional[str] = None
) -> str:
    """
    Constructs an anchored search query with corporate identity tokens
    to minimize homonym collisions and disambiguate similarly named entities.
    Example: '"Apex Synthetic Holdings (Demo)" AND ("U29253DL2018PTC334120" OR "Rajesh Kumar")'
    """
    clean_company = company_name.strip().replace('"', '')
    clean_cin = cin.strip().replace('"', '') if cin and cin != "UNREGISTERED_MSME" else None
    clean_promoter = promoter_name.strip().replace('"', '') if promoter_name else None

    if clean_cin and clean_promoter:
        return f'"{clean_company}" AND ("{clean_cin}" OR "{clean_promoter}")'
    elif clean_cin:
        return f'"{clean_company}" AND "{clean_cin}"'
    elif clean_promoter:
        return f'"{clean_company}" AND "{clean_promoter}"'
    else:
        return f'"{clean_company}"'


# ============================================================================
# 2. KEYWORD & PATTERN CLASSIFIER (No LLM Semantic Hallucinations)
# ============================================================================

# Strict keyword dictionaries for plain classification
SEVERE_PATTERNS = [
    r"\bsection\s*(?:7|9|10)\s+admitted\b",
    r"\bnclt\b.*?\badmitted\b",
    r"\bcirp\b.*?\binitiated\b",
    r"\bcorporate insolvency resolution process\b.*?\badmitted\b",
    r"\bliquidation order\b",
    r"\border of liquidation\b",
    r"\bmoratorium declared under section 14\b",
    r"\bwinding up order\b",
]

REVIEW_PATTERNS = [
    r"\bdggi\b.*?\bsummons\b",
    r"\bdirectorate general of gst intelligence\b",
    r"\btax evasion notice\b",
    r"\bshow cause notice\b",
    r"\bnclt\b.*?\bpetition filed\b",
    r"\bsection\s*(?:7|9)\s+filed\b",
    r"\benforcement directorate\b",
    r"\bed attaches\b",
    r"\bcbi inquiry\b",
    r"\bsfio investigation\b",
    r"\bsebi penalty\b",
    r"\bdemand notice\b",
]

INFORMATIONAL_PATTERNS = [
    r"\bresignation\b",
    r"\bdispute\b",
    r"\bstrike\b",
    r"\blayoffs?\b",
    r"\bnegative\b",
    r"\bcontroversy\b",
    r"\blawsuit\b",
    r"\ballegations?\b",
    r"\bnet loss\b",
    r"\bdebt default\b",
    r"\bexpansion\b",
]

# Explicit relief or dismissal phrases: must NEVER classify as SEVERE
NEGATIVE_RELIEF_PATTERNS = [
    r"\bpetition\s+(?:dismissed|rejected|withdrawn|settled)\b",
    r"\bproceedings\s+(?:dismissed|quashed|stayed|withdrawn|settled)\b",
    r"\bwithdrawn\b",
    r"\bsettled\s+(?:out\s+of\s+court|amicably)?\b",
    r"\bno\s+(?:insolvency|proceedings|adverse\s+findings)\b",
    r"\bdisposed\s+of\b",
]


def classify_finding_tier(title: str, snippet: str) -> FindingTier:
    """
    Deterministic rule-based tier classification using regex pattern matching.
    Grounds findings without claiming 'semantic classification'.
    """
    full_text = f"{title} {snippet}".lower()

    # Guard: If explicit relief or dismissal phrase is present, NEVER flag as SEVERE
    has_relief = any(re.search(pat, full_text) for pat in NEGATIVE_RELIEF_PATTERNS)

    # 1. Check Admitted Insolvency (Severe) only if no relief/dismissal indicator
    if not has_relief:
        for pattern in SEVERE_PATTERNS:
            if re.search(pattern, full_text):
                return FindingTier.SEVERE

    # 2. Check Statutory Inquiries (Review)
    for pattern in REVIEW_PATTERNS:
        if re.search(pattern, full_text):
            return FindingTier.REVIEW

    # 3. Fallback to Informational
    return FindingTier.INFORMATIONAL


# ============================================================================
# 3. SYNTHETIC DEMO GENERATOR (Strict Watermarking)
# ============================================================================

def generate_synthetic_demo_findings(
    company_name: str,
    cin: Optional[str] = None
) -> List[AdverseMediaFinding]:
    """
    Generates deterministic synthetic findings for demonstration/offline purposes.
    MANDATORY REQUIREMENT: Every headline, snippet, and case identifier is strictly
    prefixed with [SYNTHETIC DEMO DATA] across both the UI and CAM exports.
    """
    company_lower = company_name.lower()
    findings: List[AdverseMediaFinding] = []

    if "kaveri" in company_lower or "distressed" in company_lower or "fictitious" in company_lower:
        fictional_name = "Kaveri Fictitious Freight (Demo)"
        findings.append(AdverseMediaFinding(
            id=str(uuid.uuid4())[:8],
            tier=FindingTier.SEVERE,
            title=f"[SYNTHETIC DEMO DATA] [Unverified Media Mention] NCLT Bengaluru Bench Admitted Section 9 Petition against {fictional_name} (CP(IB)/102/BB/2026)",
            snippet=f"[SYNTHETIC DEMO DATA] [Unverified Media Mention] The National Company Law Tribunal (NCLT) Bengaluru Bench has admitted the insolvency petition filed by operational creditor under Section 9 of IBC. Interim Resolution Professional (IRP) appointed; moratorium declared under Section 14.",
            source_url="https://example.com/orders/2026/synthetic/kaveri_sec9.pdf",
            review_status=ReviewStatus.UNREVIEWED,
            is_synthetic=True
        ))
        findings.append(AdverseMediaFinding(
            id=str(uuid.uuid4())[:8],
            tier=FindingTier.REVIEW,
            title=f"[SYNTHETIC DEMO DATA] [Unverified Media Mention] DGGI Zonal Unit issues Summons to {fictional_name} regarding ITC Discrepancy",
            snippet=f"[SYNTHETIC DEMO DATA] [Unverified Media Mention] Directorate General of GST Intelligence (DGGI) issued formal summons regarding ₹1.45 Cr input tax credit (ITC) reconciliation mismatch for FY 2024-25.",
            source_url="https://example.com/dggi/2026/synthetic/summons_4891.html",
            review_status=ReviewStatus.UNREVIEWED,
            is_synthetic=True
        ))
    elif "bharat" in company_lower or "mismatch" in company_lower or "mock" in company_lower:
        fictional_name = "Bharat Mock Steels & Infra (Demo)"
        findings.append(AdverseMediaFinding(
            id=str(uuid.uuid4())[:8],
            tier=FindingTier.REVIEW,
            title=f"[SYNTHETIC DEMO DATA] [Unverified Media Mention] Commercial Taxes Authority issues Show Cause Notice to {fictional_name}",
            snippet=f"[SYNTHETIC DEMO DATA] [Unverified Media Mention] State GST audit wing issued inquiry notice seeking physical verification of outward turnover and e-way bill reconciliations.",
            source_url="https://example.com/notices/2026/synthetic/scn_9912.html",
            review_status=ReviewStatus.UNREVIEWED,
            is_synthetic=True
        ))
        findings.append(AdverseMediaFinding(
            id=str(uuid.uuid4())[:8],
            tier=FindingTier.INFORMATIONAL,
            title=f"[SYNTHETIC DEMO DATA] [Unverified Media Mention] Steel commodity price fluctuations compress margins for {fictional_name}",
            snippet=f"[SYNTHETIC DEMO DATA] [Unverified Media Mention] Secondary steel and structural fabrication manufacturers faced raw material price headwinds during the recent fiscal quarter.",
            source_url="https://example.com/market-reports/2026/synthetic/msme_analysis.html",
            review_status=ReviewStatus.UNREVIEWED,
            is_synthetic=True
        ))
    else:
        # Prime / Default Cohort: Informational news only
        fictional_name = "Apex Synthetic Holdings (Demo)"
        findings.append(AdverseMediaFinding(
            id=str(uuid.uuid4())[:8],
            tier=FindingTier.INFORMATIONAL,
            title=f"[SYNTHETIC DEMO DATA] [Unverified Media Mention] {fictional_name} commissions modernized precision manufacturing plant",
            snippet=f"[SYNTHETIC DEMO DATA] [Unverified Media Mention] Engineering enterprise inaugurates enhanced tooling bay with CNC machinery; management reports stable order pipeline.",
            source_url="https://example.com/industry/2026/synthetic/apex_expansion.html",
            review_status=ReviewStatus.UNREVIEWED,
            is_synthetic=True
        ))

    return findings


# ============================================================================
# 4. ADVERSE MEDIA SEARCH & FAIL-SAFE INGESTION
# ============================================================================

def search_adverse_media(
    company_name: str,
    cin: Optional[str] = None,
    promoter_name: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: int = 8,
    mock_mode: bool = False,
    http_session: Optional[Any] = None
) -> AdverseMediaReport:
    """
    Executes adverse media screening against Tavily Search API with fail-safe error handling.
    
    Guarantees:
    - If mock_mode is True, returns synthetic fixtures watermarked with [SYNTHETIC DEMO DATA].
    - If TAVILY_API_KEY is missing, times out, or errors: returns status 'RESEARCH_UNAVAILABLE'.
      Under NO circumstances does an error default to 'Clean' or 'No Adverse Findings'.
    - Disambiguates search query with company name and CIN / promoter name.
    """
    query = build_search_query(company_name, cin, promoter_name)

    # 1. Explicit Synthetic Demo Mode
    if mock_mode:
        synthetic_findings = generate_synthetic_demo_findings(company_name, cin)
        return AdverseMediaReport(
            status="COMPLETED",
            findings=synthetic_findings,
            query_used=query,
            is_synthetic_demo=True,
            error_message=None
        )

    # 2. Check for Missing API Key (Fail-Safe: Never default to Clean)
    resolved_key = api_key or os.getenv("TAVILY_API_KEY")
    if not resolved_key:
        return AdverseMediaReport(
            status="RESEARCH_UNAVAILABLE",
            findings=[],
            query_used=query,
            is_synthetic_demo=False,
            error_message="TAVILY_API_KEY is not configured in environment. Adverse media screening unavailable."
        )

    # 3. Live Tavily API Call with Defensive Timeout Handling
    try:
        session = http_session or requests
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": resolved_key,
            "query": f"{query} AND (NCLT OR insolvency OR DGGI OR IBC OR court OR fraud OR default OR controversy)",
            "search_depth": "advanced",
            "max_results": 5,
            "include_domains": [
                "ibbi.gov.in",
                "nclt.gov.in",
                "indiankanoon.org",
                "economictimes.indiatimes.com",
                "business-standard.com",
                "livemint.com",
                "taxscan.in",
                "barandbench.com"
            ]
        }
        
        resp = session.post(url, json=payload, timeout=timeout)
        if resp.status_code != 200:
            return AdverseMediaReport(
                status="RESEARCH_UNAVAILABLE",
                findings=[],
                query_used=query,
                is_synthetic_demo=False,
                error_message=f"Tavily API responded with HTTP {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()
        results = data.get("results", [])

        if not results:
            return AdverseMediaReport(
                status="NO_ADVERSE_FINDINGS",
                findings=[],
                query_used=query,
                is_synthetic_demo=False,
                error_message=None,
                disclaimer="No adverse findings in screened sources; this is not a legal clearance."
            )

        findings: List[AdverseMediaFinding] = []
        for r in results:
            raw_title = r.get("title", "Untitled Notice")
            title = f"[Unverified Media Mention] {raw_title}"
            snippet = r.get("content", "")
            source_url = r.get("url", "https://ibbi.gov.in")
            tier = classify_finding_tier(raw_title, snippet)

            findings.append(AdverseMediaFinding(
                id=str(uuid.uuid4())[:8],
                tier=tier,
                title=title,
                snippet=snippet,
                source_url=source_url,
                review_status=ReviewStatus.UNREVIEWED,
                is_synthetic=False
            ))

        return AdverseMediaReport(
            status="COMPLETED",
            findings=findings,
            query_used=query,
            is_synthetic_demo=False,
            error_message=None
        )

    except (requests.Timeout, requests.RequestException, Exception) as e:
        # Network failure or timeout -> RESEARCH_UNAVAILABLE (Defensive fail-closed)
        return AdverseMediaReport(
            status="RESEARCH_UNAVAILABLE",
            findings=[],
            query_used=query,
            is_synthetic_demo=False,
            error_message=f"Network error during adverse media search: {str(e)}"
        )


SYNTHETIC_DOCKET_ARCHIVE = {
    "kaveri_sec9": (
        "BEFORE THE HON'BLE NATIONAL COMPANY LAW TRIBUNAL, BENGALURU BENCH\n"
        "COMPANY PETITION (IB) NO. 102/BB/2026\n"
        "Under Section 9 of the Insolvency and Bankruptcy Code, 2016 r/w Rule 6 of IBC Rules.\n\n"
        "IN THE MATTER OF:\n"
        "M/s Trans-India Fuel Logistics Pvt Ltd ... Operational Creditor\n"
        "VERSUS\n"
        "M/s Kaveri Fictitious Freight (Demo) [CIN: U60200KA2019PTC120481] ... Corporate Debtor\n\n"
        "ORDER PRONOUNCED ON: 14.02.2026\n"
        "CORAM: Hon'ble Member (Judicial) & Hon'ble Member (Technical)\n\n"
        "1. This is an application filed under Section 9 of the IBC, 2016 seeking initiation of Corporate Insolvency Resolution Process (CIRP) against Kaveri Fictitious Freight (Demo) for an operational default amounting to ₹ 1,42,85,000/- towards unpaid fleet diesel logistics invoices.\n"
        "2. The Corporate Debtor was served statutory demand notice under Section 8(1) of the Code on 12.11.2025. No notice of dispute under Section 8(2) was received within the statutory 10-day period.\n"
        "3. Perusal of bank statements and debt certificates under Section 9(3)(c) demonstrates unpaid operational debt exceeding the statutory threshold under Section 4 of the Code.\n"
        "4. In view of the above facts, this Tribunal finds the application complete in all respects. The petition is ADMITTED.\n"
        "5. Moratorium under Section 14 is declared. Shri R. K. Nambiar (Reg: IBBI/IPA-001/IP-P00812/2021) is appointed Interim Resolution Professional (IRP)."
    ),
    "summons_4891": (
        "GOVERNMENT OF INDIA\n"
        "MINISTRY OF FINANCE, DEPARTMENT OF REVENUE\n"
        "DIRECTORATE GENERAL OF GST INTELLIGENCE (DGGI), ZONAL UNIT\n"
        "SUMMONS UNDER SECTION 70 OF THE CGST ACT, 2017\n\n"
        "F. No. DGGI/ZU/INV/GST-ITC/4891/2026\n"
        "Dated: 18.01.2026\n\n"
        "TO: The Managing Director, M/s Kaveri Fictitious Freight (Demo), Bangalore [CIN: U60200KA2019PTC120481]\n\n"
        "SUBJECT: Inward Input Tax Credit (ITC) investigation under Section 16(2)(c) & Rule 36(4).\n\n"
        "WHEREAS, an inquiry is being conducted into suspicious Circular Invoicing and ineligible Input Tax Credit (ITC) claims aggregating to ₹ 1.45 Crores availed during FY 2024-25 from struck-off suppliers.\n\n"
        "NOW THEREFORE, in exercise of powers conferred under Section 70 of the Central Goods and Services Tax Act, 2017, you are hereby summoned to appear before the undersigned Superintendent at DGGI Zonal Headquarters along with outward GSTR-1, GSTR-3B filings, and bank reconciliation ledgers."
    ),
    "scn_9912": (
        "COMMERCIAL TAXES DEPARTMENT, AUDIT DIVISION\n"
        "NOTICE UNDER SECTION 65 OF STATE GST ACT, 2017\n\n"
        "Reference: SCN/AUDIT/2026/9912\n"
        "Entity: Bharat Mock Steels & Infra (Demo) [CIN: U24101MH2020PTC349120]\n\n"
        "SUBJECT: Audit observation regarding high-ratio e-Way bill cancellations vs outward GSTR-3B filings.\n\n"
        "Comprehensive automated scanning identified 42 instances of cancelled e-Way bills corresponding to steel consignment deliveries, resulting in an unverified turnover variance of ₹ 2.80 Crores. Entity is granted 15 days to furnish consignment vehicle toll logs and weighbridge certificates."
    ),
    "msme_analysis": (
        "COMMODITY & INDUSTRIAL CREDIT BULLETIN\n"
        "SECTOR SPOTLIGHT: SECONDARY STEEL FABRICATION & INFRASTRUCTURE MSMES\n\n"
        "Report ID: IND-STL-Q3-2026\n"
        "Monitored Entity: Bharat Mock Steels & Infra (Demo)\n\n"
        "Observation: Secondary steel processors experienced margin compression of 280 bps due to imported coking coal escalation. High gross turnover (₹14.5 Cr) contrasts with thin operating margins (<2.5%), necessitating heightened working capital liquidity monitoring and debtor aging surveillance."
    ),
    "apex_expansion": (
        "MINISTRY OF CORPORATE AFFAIRS / ROC DELHI\n"
        "STATUTORY CHARGE SATISFACTION CERTIFICATE (FORM CHG-4)\n\n"
        "Company: Apex Synthetic Holdings (Demo) [CIN: U29253DL2018PTC334120]\n"
        "Charge ID: 100482910 | Principal Amount: ₹ 3,50,00,000/-\n"
        "Charge Holder: State Bank of India, Commercial Branch\n\n"
        "Certification: Registrar of Companies confirms satisfaction in full of the charge registered on 12.04.2021. Register of Charges updated. Zero active default charges or encumbrances pending on MCA-21 record."
    ),
}


def extract_legal_docket_text(url: str, api_key: Optional[str] = None, timeout: int = 15) -> Optional[str]:
    """
    Extracts the full text of a judicial judgment, NCLT order, or regulatory notice
    using Tavily's extract API endpoint (client.extract).
    For synthetic benchmark dockets, returns authentic simulated order transcripts offline.
    """
    if not url:
        return None
    url_lower = url.lower()
    for k, text in SYNTHETIC_DOCKET_ARCHIVE.items():
        if k in url_lower:
            return text
    if "synthetic" in url_lower or "example.com" in url_lower:
        return SYNTHETIC_DOCKET_ARCHIVE["kaveri_sec9"]

    resolved_key = api_key or os.getenv("TAVILY_API_KEY")
    if not resolved_key:
        return None
    try:
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=resolved_key)
            res = client.extract(urls=[url])
            results = res.get("results", [])
            if results and "raw_content" in results[0]:
                return results[0]["raw_content"]
        except ImportError:
            pass

        # Fallback to direct REST endpoint
        resp = requests.post(
            "https://api.tavily.com/extract",
            json={"api_key": resolved_key, "urls": [url]},
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if results and "raw_content" in results[0]:
                return results[0]["raw_content"]
    except Exception as e:
        logger.warning(f"Tavily extract failed for {url}: {e}")
    return None


def crawl_registry_domain(base_url: str, api_key: Optional[str] = None, extract_depth: str = "advanced") -> Optional[Dict[str, Any]]:
    """
    Crawls a regulatory or registry domain hierarchy using Tavily's crawl API endpoint (client.crawl).
    Used for comprehensive audits of corporate registries or judicial benches.
    """
    resolved_key = api_key or os.getenv("TAVILY_API_KEY")
    if not resolved_key or not base_url:
        return None
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=resolved_key)
        return client.crawl(url=base_url, extract_depth=extract_depth)
    except Exception as e:
        logger.warning(f"Tavily crawl failed for {base_url}: {e}")
    return None


# ============================================================================
# 5. TRIPARTITE REVIEW STATE TRANSITIONS (HITL Governance)
# ============================================================================

def confirm_adverse_finding(
    report: AdverseMediaReport,
    finding_id: str,
    justification: str,
    officer_name: str = "Loan Officer"
) -> AdverseMediaReport:
    """
    Transitions finding to CONFIRMED with mandatory written justification (order/ref number).
    Only confirmed findings will forward risk flags into the policy gate.
    """
    if not justification or not justification.strip():
        raise ValueError("Officer confirmation requires a non-empty justification note (e.g. court order or reference number).")

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    updated_findings = []
    for f in report.findings:
        if f.id == finding_id:
            updated_f = f.model_copy(update={
                "review_status": ReviewStatus.CONFIRMED,
                "confirmation_note": justification.strip(),
                "reviewed_by": officer_name,
                "reviewed_at": now_iso
            })
            updated_findings.append(updated_f)
        else:
            updated_findings.append(f)

    return report.model_copy(update={"findings": updated_findings})


def merge_existing_reviews(
    new_report: AdverseMediaReport,
    existing_report: Optional[AdverseMediaReport]
) -> AdverseMediaReport:
    """
    Preserves existing officer determinations (CONFIRMED / DISMISSED) across successive
    scans by keying reviews by finding ID or URL match, preventing re-scans from wiping reviews.
    """
    if not existing_report or not existing_report.findings:
        return new_report

    review_map: Dict[str, AdverseMediaFinding] = {}
    for f in existing_report.findings:
        if f.review_status != ReviewStatus.UNREVIEWED:
            review_map[f.id] = f
            if f.source_url:
                review_map[f.source_url] = f

    merged_findings: List[AdverseMediaFinding] = []
    for nf in new_report.findings:
        prior = review_map.get(nf.id) or (review_map.get(nf.source_url) if nf.source_url else None)
        if prior:
            merged_findings.append(nf.model_copy(update={
                "review_status": prior.review_status,
                "dismissal_reason": prior.dismissal_reason,
                "confirmation_note": prior.confirmation_note,
                "reviewed_by": prior.reviewed_by,
                "reviewed_at": prior.reviewed_at
            }))
        else:
            merged_findings.append(nf)

    return new_report.model_copy(update={"findings": merged_findings})


def dismiss_adverse_finding(
    report: AdverseMediaReport,
    finding_id: str,
    justification: str,
    officer_name: str = "Loan Officer"
) -> AdverseMediaReport:
    """
    Transitions finding to DISMISSED with a mandatory written justification.
    Dismissed findings have zero effect on policy gates.
    """
    if not justification or len(justification.strip()) < 10:
        raise ValueError("Officer dismissal requires a written justification of at least 10 characters.")

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    updated_findings = []
    for f in report.findings:
        if f.id == finding_id:
            updated_f = f.model_copy(update={
                "review_status": ReviewStatus.DISMISSED,
                "dismissal_reason": justification.strip(),
                "reviewed_by": officer_name,
                "reviewed_at": now_iso
            })
            updated_findings.append(updated_f)
        else:
            updated_findings.append(f)

    return report.model_copy(update={"findings": updated_findings})


# ============================================================================
# 6. POLICY GATE BRIDGE (Pure Function Extraction)
# ============================================================================

def extract_policy_flags(
    report: Optional[AdverseMediaReport]
) -> Tuple[List[str], List[str]]:
    """
    Extracts policy gate flags strictly from officer-CONFIRMED findings.
    
    Rules:
    - UNREVIEWED findings -> Ignored (0 effect on policy gate).
    - DISMISSED findings  -> Ignored (0 effect on policy gate).
    - CONFIRMED SEVERE    -> Added to severe_flags (Forces Verdict.REJECT).
    - CONFIRMED REVIEW    -> Added to review_flags (Caps verdict at Verdict.CONDITIONAL).
    
    Returns:
    (severe_flags, review_flags)
    """
    if report is None or report.status != "COMPLETED":
        return [], []

    severe_flags: List[str] = []
    review_flags: List[str] = []

    for f in report.findings:
        if f.review_status == ReviewStatus.CONFIRMED:
            if f.tier == FindingTier.SEVERE:
                severe_flags.append(f"CONFIRMED_INSOLVENCY: {f.title}")
            elif f.tier == FindingTier.REVIEW:
                review_flags.append(f"CONFIRMED_STATUTORY_INQUIRY: {f.title}")

    return severe_flags, review_flags
