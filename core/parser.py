import os
import re
import time
import json
import logging
from decimal import Decimal
from typing import Optional, Dict, Any, List, Tuple
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

import pymupdf

from core.config import GEMINI_API_KEY, MODEL_EXTRACTION
from core.data_models import (
    BankExtraction,
    GSTExtraction,
    ITRExtraction,
    CreditCase,
    coerce_currency,
)

logger = logging.getLogger("creditgate.parser")
logging.basicConfig(level=logging.INFO)

# ============================================================================
# 1. PYMUPDF SLICING & HEURISTIC ANCHOR DETECTION
# ============================================================================

DOC_ANCHORS = {
    "bank": [
        "opening balance", "closing balance", "total credits", "total debits",
        "statement period", "transaction", "ledger", "account statement", "inward", "outward"
    ],
    "gst": [
        "taxable value", "gstr-3b", "outward taxable supplies", "eligible itc",
        "tax payable", "total tax paid", "igst", "cgst", "sgst"
    ],
    "itr": [
        "gross total income", "itr-v", "profit after tax", "depreciation",
        "interest paid", "net worth", "total debt", "assessment year", "existing emi"
    ]
}


def slice_relevant_pages(pdf_bytes: bytes, doc_type: str, max_pages: int = 5) -> Tuple[bytes, List[int]]:
    """
    Scans PDF byte stream for anchor keywords and slices only the most relevant pages.
    Prevents flooding multimodal vision models with redundant 500-page bank statements.
    """
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        if total_pages <= max_pages:
            sliced_pages = list(range(total_pages))
            logger.info(f"Document has {total_pages} pages (<= {max_pages}), retaining full stream.")
            return pdf_bytes, sliced_pages

        anchors = DOC_ANCHORS.get(doc_type.lower(), [])
        page_scores = []

        for page_idx in range(total_pages):
            page_text = doc[page_idx].get_text("text").lower()
            score = sum(1 for anchor in anchors if anchor in page_text)
            page_scores.append((score, page_idx))

        # Sort by relevance score, preserving first page and top matching pages
        selected_indices = {0}  # Always include summary/header page 1
        top_scored = [idx for score, idx in sorted(page_scores, key=lambda x: x[0], reverse=True) if score > 0]
        for idx in top_scored:
            selected_indices.add(idx)
            if len(selected_indices) >= max_pages:
                break

        # Fallback if no keywords matched
        if len(selected_indices) < 2 and total_pages > 1:
            selected_indices.add(1)

        sorted_pages = sorted(list(selected_indices))
        logger.info(f"Slicing {doc_type} PDF from {total_pages} pages down to pages: {sorted_pages}")

        new_doc = pymupdf.open()
        for idx in sorted_pages:
            new_doc.insert_pdf(doc, from_page=idx, to_page=idx)

        sliced_bytes = new_doc.tobytes()
        new_doc.close()
        doc.close()
        return sliced_bytes, sorted_pages

    except Exception as e:
        logger.warning(f"Error during PDF slicing for {doc_type}: {e}. Retaining original bytes.")
        return pdf_bytes, [0]


# ============================================================================
# 2. BYTE-LEVEL PII REDACTION
# ============================================================================

PAN_PATTERN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
AADHAAR_PATTERN = re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b|\b\d{12}\b")


def redact_pii(pdf_bytes: bytes) -> Tuple[bytes, List[Dict[str, Any]]]:
    """
    Performs true byte-level PII redaction on selectable text layers.
    Scans for PAN and Aadhaar patterns, applies redaction annotations,
    and removes underlying text bytes via page.apply_redactions().
    Logs REDACTION_SKIPPED if the page is image-only without text.
    """
    audit_events: List[Dict[str, Any]] = []
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        for page_idx, page in enumerate(doc):
            text = page.get_text("text")
            if not text.strip():
                audit_events.append({
                    "event": "PII_REDACTION_SKIPPED",
                    "page": page_idx + 1,
                    "reason": "NO_TEXT_LAYER_FOUND"
                })
                continue

            # Redact PAN
            for match in PAN_PATTERN.finditer(text):
                pan_val = match.group()
                rects = page.search_for(pan_val)
                for rect in rects:
                    page.add_redact_annot(rect, fill=(0, 0, 0))
                audit_events.append({
                    "event": "PII_REDACTED",
                    "type": "PAN",
                    "masked_val": f"{pan_val[:2]}***{pan_val[-2:]}",
                    "page": page_idx + 1
                })

            # Redact Aadhaar
            for match in AADHAAR_PATTERN.finditer(text):
                aadhaar_val = match.group()
                rects = page.search_for(aadhaar_val)
                for rect in rects:
                    page.add_redact_annot(rect, fill=(0, 0, 0))
                audit_events.append({
                    "event": "PII_REDACTED",
                    "type": "AADHAAR",
                    "masked_val": "XXXX-XXXX-XXXX",
                    "page": page_idx + 1
                })

            page.apply_redactions()

        sanitized_bytes = doc.tobytes()
        doc.close()
        return sanitized_bytes, audit_events

    except Exception as e:
        logger.error(f"Error during PII redaction: {e}")
        return pdf_bytes, [{"event": "PII_REDACTION_ERROR", "error": str(e)}]


# ============================================================================
# 3. DETERMINISTIC PARSING FALLBACK (OFFLINE / ZERO-KEY MODE)
# ============================================================================

def find_amount_after_label(text: str, label_pattern: str, search_window: int = 400) -> Optional[str]:
    """Searches for a label pattern, then extracts the nearest currency string in the subsequent window."""
    m = re.search(label_pattern, text, re.IGNORECASE)
    if not m:
        return None
    sub = text[m.end():m.end() + search_window]
    num_m = re.search(r"₹?\s*([0-9]{1,3}(?:,[0-9]{2,3})*(?:\.[0-9]{2})?)", sub)
    if num_m:
        return num_m.group(1).strip()
    return None


def _extract_text_deterministic_bank(pdf_bytes: bytes) -> BankExtraction:
    """Extracts Bank fields directly from text layer with multi-layout resilience."""
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        full_text = "\n".join(page.get_text("text") for page in doc)
        doc.close()
    except Exception as e:
        logger.error(f"Error reading bank PDF: {e}")
        return BankExtraction(confidence="LOW", extraction_engine="DETERMINISTIC_FALLBACK")

    bounce_count = 0

    # Pattern A: Sequential table row (Chrome table rendering outputs all 5 th then all 5 td)
    seq_match = re.search(
        r"Total Inward/Outward\s+Bounces\s*(?:\n\s*)*₹?\s*([0-9,]+\.[0-9]{2})\s*(?:\n\s*)*₹?\s*([0-9,]+\.[0-9]{2})\s*(?:\n\s*)*₹?\s*([0-9,]+\.[0-9]{2})\s*(?:\n\s*)*₹?\s*([0-9,]+\.[0-9]{2})\s*(?:\n\s*)*(\d+)",
        full_text,
        re.IGNORECASE
    )
    if seq_match:
        open_bal = seq_match.group(1)
        total_cr = seq_match.group(2)
        total_dr = seq_match.group(3)
        close_bal = seq_match.group(4)
        bounce_count = int(seq_match.group(5))
    else:
        # Pattern B: Inline key-value pairs
        open_bal = find_amount_after_label(full_text, r"Opening Balance")
        total_cr = find_amount_after_label(full_text, r"Total Inward Credits|Total Credits")
        total_dr = find_amount_after_label(full_text, r"Total Outward Debits|Total Debits")
        close_bal = find_amount_after_label(full_text, r"Closing Balance")
        
        # Check bounces from ledger narrations
        bounces = re.findall(r"CHQ RTN:|\bECS RETURN:|\bINWARD CLG RET:|\bNACH RET:", full_text, re.IGNORECASE)
        bounce_count = len(bounces)

    # If label was not found, return None + LOW confidence (do NOT default to 0.00)
    has_missing = any(v is None for v in [open_bal, total_cr, total_dr, close_bal])
    conf = "LOW" if has_missing else "HIGH"

    return BankExtraction(
        total_credits=coerce_currency(total_cr) if total_cr is not None else None,
        total_debits=coerce_currency(total_dr) if total_dr is not None else None,
        opening_balance=coerce_currency(open_bal) if open_bal is not None else None,
        closing_balance=coerce_currency(close_bal) if close_bal is not None else None,
        bounce_count=bounce_count,
        confidence=conf,
        extraction_engine="DETERMINISTIC_FALLBACK"
    )


def _extract_text_deterministic_gst(pdf_bytes: bytes) -> GSTExtraction:
    """Extracts GST fields directly from text layer."""
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        full_text = "\n".join(page.get_text("text") for page in doc)
        doc.close()
    except Exception as e:
        logger.error(f"Error reading GST PDF: {e}")
        return GSTExtraction(confidence="LOW", extraction_engine="DETERMINISTIC_FALLBACK")

    taxable = (
        find_amount_after_label(full_text, r"Total Statutory Liability Declared")
        or find_amount_after_label(full_text, r"Outward taxable supplies \(other than zero rated")
        or find_amount_after_label(full_text, r"Total Taxable Value")
    )

    tax_paid = (
        find_amount_after_label(full_text, r"Total Tax Paid\s*[:\-]?")
        or find_amount_after_label(full_text, r"Payment of Tax")
    )

    has_missing = (taxable is None) or (tax_paid is None)
    conf = "LOW" if has_missing else "HIGH"

    return GSTExtraction(
        taxable_value=coerce_currency(taxable) if taxable is not None else None,
        total_tax_paid=coerce_currency(tax_paid) if tax_paid is not None else None,
        confidence=conf,
        extraction_engine="DETERMINISTIC_FALLBACK"
    )


def _extract_text_deterministic_itr(pdf_bytes: bytes) -> ITRExtraction:
    """Extracts ITR fields directly from text layer."""
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        full_text = "\n".join(page.get_text("text") for page in doc)
        doc.close()
    except Exception as e:
        logger.error(f"Error reading ITR PDF: {e}")
        return ITRExtraction(confidence="LOW", extraction_engine="DETERMINISTIC_FALLBACK")

    gti = (
        find_amount_after_label(full_text, r"Gross Total Turnover\s*/\s*Revenue from Operations")
        or find_amount_after_label(full_text, r"Gross Total Income")
    )

    depr = (
        find_amount_after_label(full_text, r"Depreciation and Amortization Expenses")
        or find_amount_after_label(full_text, r"Depreciation")
    )

    interest = (
        find_amount_after_label(full_text, r"Finance Costs\s*/\s*Interest Expense Paid")
        or find_amount_after_label(full_text, r"Interest Paid")
    )

    pat = (
        find_amount_after_label(full_text, r"Profit After Tax \(PAT\)\s*/\s*Net Profit")
        or find_amount_after_label(full_text, r"Profit After Tax")
    )

    nw = (
        find_amount_after_label(full_text, r"Total Tangible Net Worth")
        or find_amount_after_label(full_text, r"Net Worth")
    )

    debt = (
        find_amount_after_label(full_text, r"Existing Debt\s*/\s*Long-Term Borrowings Outstanding")
        or find_amount_after_label(full_text, r"Total Debt")
    )

    emi = (
        find_amount_after_label(full_text, r"Current Annual Debt Service\s*/\s*Existing EMI Obligations")
        or find_amount_after_label(full_text, r"Existing EMI Obligations")
    )

    fields = [gti, pat, depr, interest, nw, debt, emi]
    has_missing = any(f is None for f in fields)
    conf = "LOW" if has_missing else "HIGH"

    return ITRExtraction(
        gross_total_income=coerce_currency(gti) if gti is not None else None,
        profit_after_tax=coerce_currency(pat) if pat is not None else None,
        depreciation=coerce_currency(depr) if depr is not None else None,
        interest_paid=coerce_currency(interest) if interest is not None else None,
        net_worth=coerce_currency(nw) if nw is not None else None,
        total_debt=coerce_currency(debt) if debt is not None else None,
        existing_emi_obligations=coerce_currency(emi) if emi is not None else None,
        confidence=conf,
        extraction_engine="DETERMINISTIC_FALLBACK"
    )


# ============================================================================
# 4. MULTIMODAL EXTRACTION WITH GEMINI 3.5 FLASH LITE
# ============================================================================

class GeminiBankExtractionSchema(BaseModel):
    total_credits: Optional[float] = None
    total_debits: Optional[float] = None
    opening_balance: Optional[float] = None
    closing_balance: Optional[float] = None
    bounce_count: Optional[int] = None
    confidence: str = Field(default="HIGH")


class GeminiGSTExtractionSchema(BaseModel):
    taxable_value: Optional[float] = None
    total_tax_paid: Optional[float] = None
    confidence: str = Field(default="HIGH")


class GeminiITRExtractionSchema(BaseModel):
    gross_total_income: Optional[float] = None
    profit_after_tax: Optional[float] = None
    depreciation: Optional[float] = None
    interest_paid: Optional[float] = None
    net_worth: Optional[float] = None
    total_debt: Optional[float] = None
    existing_emi_obligations: Optional[float] = None
    confidence: str = Field(default="HIGH")


def _call_gemini_vision(pdf_bytes: bytes, system_prompt: str, schema_cls: Any, api_key: Optional[str] = None) -> Any:
    """
    Calls Gemini API passing sanitized inline bytes directly without temporary disk files.
    Enforces structured JSON output conforming to the specified clean Pydantic schema.
    Falls back gracefully to deterministic extraction if API key is unset or network fails.
    """
    if os.getenv("PYTEST_CURRENT_TEST") and not api_key:
        return None

    key_to_use = api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY
    if not key_to_use or key_to_use == "your_gemini_api_key_here":
        logger.info("GEMINI_API_KEY not configured. Engaging deterministic fallback parser.")
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key_to_use)
        pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=schema_cls,
            temperature=0.0,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        response = client.models.generate_content(
            model=MODEL_EXTRACTION,
            contents=[pdf_part, "Extract the required structured financial fields from this document."],
            config=config
        )

        if response.text:
            parsed_json = json.loads(response.text)
            inst = schema_cls(**parsed_json)
            return inst
        return None

    except Exception as e:
        logger.warning(f"Gemini API call failed: {e}. Falling back to deterministic parser.")
        return None


def extract_bank_statement(
    pdf_bytes: bytes,
    gemini_api_key: Optional[str] = None,
    force_deterministic: bool = False
) -> BankExtraction:
    """Slices, redacts, and extracts Bank Statement into BankExtraction."""
    sliced_bytes, _ = slice_relevant_pages(pdf_bytes, doc_type="bank", max_pages=3)
    redacted_bytes, audit_events = redact_pii(sliced_bytes)

    has_image_only = any(e.get("event") == "PII_REDACTION_SKIPPED" for e in audit_events)
    if has_image_only:
        return BankExtraction(
            confidence="LOW",
            extraction_engine="OFFLINE_IMAGE_UNSUPPORTED",
            redaction_events=audit_events
        )

    if not force_deterministic:
        prompt = (
            "You are an expert Indian MSME Credit Underwriter. Extract bank statement financial fields:\n"
            "- total_credits (Total inward credits/deposits for the period)\n"
            "- total_debits (Total outward debits/withdrawals for the period)\n"
            "- opening_balance\n"
            "- closing_balance\n"
            "- bounce_count (Count of inward/outward cheque returns, ECS/NACH mandate bounces)\n"
            "- confidence (HIGH if clear, LOW if blurry or ambiguous)\n"
            "Return numeric values only as standard numbers or strings without commas."
        )

        raw = _call_gemini_vision(redacted_bytes, prompt, GeminiBankExtractionSchema, api_key=gemini_api_key)
        if raw is not None:
            return BankExtraction(
                total_credits=Decimal(str(raw.total_credits)) if raw.total_credits is not None else None,
                total_debits=Decimal(str(raw.total_debits)) if raw.total_debits is not None else None,
                opening_balance=Decimal(str(raw.opening_balance)) if raw.opening_balance is not None else None,
                closing_balance=Decimal(str(raw.closing_balance)) if raw.closing_balance is not None else None,
                bounce_count=raw.bounce_count,
                confidence=raw.confidence.upper() if raw.confidence and raw.confidence.upper() in ["HIGH", "LOW"] else "HIGH",
                extraction_engine="AI_GEMINI",
                redaction_events=audit_events,
            )

    fallback = _extract_text_deterministic_bank(redacted_bytes)
    fallback.redaction_events = audit_events
    return fallback


def extract_gstr3b(
    pdf_bytes: bytes,
    gemini_api_key: Optional[str] = None,
    force_deterministic: bool = False
) -> GSTExtraction:
    """Slices, redacts, and extracts GSTR-3B into GSTExtraction."""
    sliced_bytes, _ = slice_relevant_pages(pdf_bytes, doc_type="gst", max_pages=2)
    redacted_bytes, audit_events = redact_pii(sliced_bytes)

    has_image_only = any(e.get("event") == "PII_REDACTION_SKIPPED" for e in audit_events)
    if has_image_only:
        return GSTExtraction(
            confidence="LOW",
            extraction_engine="OFFLINE_IMAGE_UNSUPPORTED",
            redaction_events=audit_events
        )

    if not force_deterministic:
        prompt = (
            "You are an expert Indian GST Tax Underwriter. Extract GSTR-3B tax summary values:\n"
            "- taxable_value (Total taxable value of outward supplies)\n"
            "- total_tax_paid (Total tax discharged/paid)\n"
            "- confidence (HIGH or LOW)\n"
            "Return clean numbers without currency symbols."
        )

        raw = _call_gemini_vision(redacted_bytes, prompt, GeminiGSTExtractionSchema, api_key=gemini_api_key)
        if raw is not None:
            return GSTExtraction(
                taxable_value=Decimal(str(raw.taxable_value)) if raw.taxable_value is not None else None,
                total_tax_paid=Decimal(str(raw.total_tax_paid)) if raw.total_tax_paid is not None else None,
                confidence=raw.confidence.upper() if raw.confidence and raw.confidence.upper() in ["HIGH", "LOW"] else "HIGH",
                extraction_engine="AI_GEMINI",
                redaction_events=audit_events,
            )

    fallback = _extract_text_deterministic_gst(redacted_bytes)
    fallback.redaction_events = audit_events
    return fallback


def extract_itr(
    pdf_bytes: bytes,
    gemini_api_key: Optional[str] = None,
    force_deterministic: bool = False
) -> ITRExtraction:
    """Slices, redacts, and extracts ITR / Audited Financials into ITRExtraction."""
    sliced_bytes, _ = slice_relevant_pages(pdf_bytes, doc_type="itr", max_pages=3)
    redacted_bytes, audit_events = redact_pii(sliced_bytes)

    has_image_only = any(e.get("event") == "PII_REDACTION_SKIPPED" for e in audit_events)
    if has_image_only:
        return ITRExtraction(
            confidence="LOW",
            extraction_engine="OFFLINE_IMAGE_UNSUPPORTED",
            redaction_events=audit_events
        )

    if not force_deterministic:
        prompt = (
            "You are an expert corporate balance sheet underwriter. Extract corporate income tax metrics:\n"
            "- gross_total_income\n"
            "- profit_after_tax\n"
            "- depreciation\n"
            "- interest_paid\n"
            "- net_worth\n"
            "- total_debt\n"
            "- existing_emi_obligations\n"
            "- confidence (HIGH or LOW)\n"
            "Return numbers without commas."
        )

        raw = _call_gemini_vision(redacted_bytes, prompt, GeminiITRExtractionSchema, api_key=gemini_api_key)
        if raw is not None:
            return ITRExtraction(
                gross_total_income=Decimal(str(raw.gross_total_income)) if raw.gross_total_income is not None else None,
                profit_after_tax=Decimal(str(raw.profit_after_tax)) if raw.profit_after_tax is not None else None,
                depreciation=Decimal(str(raw.depreciation)) if raw.depreciation is not None else None,
                interest_paid=Decimal(str(raw.interest_paid)) if raw.interest_paid is not None else None,
                net_worth=Decimal(str(raw.net_worth)) if raw.net_worth is not None else None,
                total_debt=Decimal(str(raw.total_debt)) if raw.total_debt is not None else None,
                existing_emi_obligations=Decimal(str(raw.existing_emi_obligations)) if raw.existing_emi_obligations is not None else None,
                confidence=raw.confidence.upper() if raw.confidence and raw.confidence.upper() in ["HIGH", "LOW"] else "HIGH",
                extraction_engine="AI_GEMINI",
                redaction_events=audit_events,
            )

    fallback = _extract_text_deterministic_itr(redacted_bytes)
    fallback.redaction_events = audit_events
    return fallback


# ============================================================================
# 5. DETERMINISTIC BANK RECONCILIATION VERIFICATION
# ============================================================================

def verify_bank_reconciliation(bank: BankExtraction) -> Tuple[bool, Decimal]:
    """
    Validates arithmetic integrity: Opening Balance + Total Credits - Total Debits == Closing Balance.
    Returns (is_balanced, discrepancy_amount).
    """
    if bank.opening_balance is None or bank.total_credits is None or bank.total_debits is None or bank.closing_balance is None:
        return False, Decimal("0.00")
    computed_close = bank.opening_balance + bank.total_credits - bank.total_debits
    discrepancy = abs(computed_close - bank.closing_balance)
    is_balanced = discrepancy < Decimal("1.00")
    return is_balanced, discrepancy


# ============================================================================
# 6. PARALLEL ORCHESTRATION PIPELINE
# ============================================================================

def extract_all_documents(
    bank_bytes: bytes,
    gst_bytes: bytes,
    itr_bytes: bytes,
    company_name: str = "Applicant Company",
    cin_or_pan: str = "U29253DL2018PTC334120",
    requested_loan_amount: Decimal = Decimal("5000000.00"),
    proposed_emi: Decimal = Decimal("150000.00"),
    proposed_rate: Decimal = Decimal("11.50"),
    proposed_tenure_months: int = 36,
    gemini_api_key: Optional[str] = None,
    force_deterministic: bool = False,
) -> Tuple[CreditCase, Dict[str, Any]]:
    """
    Executes parallel extraction across Bank, GST, and ITR documents using ThreadPoolExecutor(max_workers=3).
    Measures wall-clock latency, performs deterministic reconciliation, and populates CreditCase.
    """
    start_time = time.perf_counter()

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_bank = executor.submit(extract_bank_statement, bank_bytes, gemini_api_key, force_deterministic)
        future_gst = executor.submit(extract_gstr3b, gst_bytes, gemini_api_key, force_deterministic)
        future_itr = executor.submit(extract_itr, itr_bytes, gemini_api_key, force_deterministic)

        bank_data = future_bank.result()
        gst_data = future_gst.result()
        itr_data = future_itr.result()

    elapsed_time = time.perf_counter() - start_time
    is_reconciled, diff = verify_bank_reconciliation(bank_data)

    credit_case = CreditCase(
        company_name=company_name,
        cin_or_pan=cin_or_pan,
        requested_loan_amount=requested_loan_amount,
        proposed_emi=proposed_emi,
        proposed_rate=proposed_rate,
        proposed_tenure_months=proposed_tenure_months,
        bank_data=bank_data,
        gst_data=gst_data,
        itr_data=itr_data
    )

    engines = [
        getattr(bank_data, "extraction_engine", "DETERMINISTIC_FALLBACK"),
        getattr(gst_data, "extraction_engine", "DETERMINISTIC_FALLBACK"),
        getattr(itr_data, "extraction_engine", "DETERMINISTIC_FALLBACK"),
    ]
    primary_engine = "AI_GEMINI" if all(e == "AI_GEMINI" for e in engines) else "DETERMINISTIC_FALLBACK"

    all_redaction_events = (
        getattr(bank_data, "redaction_events", []) +
        getattr(gst_data, "redaction_events", []) +
        getattr(itr_data, "redaction_events", [])
    )

    has_key = bool(gemini_api_key or GEMINI_API_KEY or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
    diagnostic_code = "CONNECTED" if primary_engine == "AI_GEMINI" else ("FORCE_DETERMINISTIC" if force_deterministic else ("KEY_MISSING" if not has_key else "FALLBACK_DETERMINISTIC"))
    engine_display = "Multimodal AI (Gemini 3.5 Flash Lite)" if primary_engine == "AI_GEMINI" else "Deterministic Regex (PyMuPDF)"

    metadata = {
        "execution_latency_seconds": round(elapsed_time, 3),
        "bank_reconciled": is_reconciled,
        "bank_reconciliation_difference": str(diff),
        "extraction_model": MODEL_EXTRACTION,
        "extraction_engine": primary_engine,
        "diagnostic_code": diagnostic_code,
        "engine_display": engine_display,
        "parallel_workers": 3,
        "status": "SUCCESS",
        "redaction_events": all_redaction_events
    }

    return credit_case, metadata
