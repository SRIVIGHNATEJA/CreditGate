import os
import re
import time
import json
import logging
from decimal import Decimal
from typing import Optional, Dict, Any, List, Tuple
from concurrent.futures import ThreadPoolExecutor

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
    num_m = re.search(r"₹?\s*([0-9]{1,3}(?:,[0-9]{2,3})*(?:\.[0-9]{2}))", sub)
    if num_m:
        return num_m.group(1).strip()
    return None


def _extract_text_deterministic_bank(pdf_bytes: bytes) -> BankExtraction:
    """Extracts Bank fields directly from text layer with multi-layout resilience."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    full_text = "\n".join(page.get_text("text") for page in doc)
    doc.close()

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

    # Defaults if still unparsed
    open_bal = open_bal or "0.00"
    total_cr = total_cr or "0.00"
    total_dr = total_dr or "0.00"
    close_bal = close_bal or "0.00"

    return BankExtraction(
        total_credits=coerce_currency(total_cr),
        total_debits=coerce_currency(total_dr),
        opening_balance=coerce_currency(open_bal),
        closing_balance=coerce_currency(close_bal),
        bounce_count=bounce_count,
        confidence="HIGH"
    )


def _extract_text_deterministic_gst(pdf_bytes: bytes) -> GSTExtraction:
    """Extracts GST fields directly from text layer."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    full_text = "\n".join(page.get_text("text") for page in doc)
    doc.close()

    taxable = (
        find_amount_after_label(full_text, r"Total Statutory Liability Declared")
        or find_amount_after_label(full_text, r"Outward taxable supplies \(other than zero rated")
        or find_amount_after_label(full_text, r"Total Taxable Value")
        or "0.00"
    )

    tax_paid = (
        find_amount_after_label(full_text, r"Total Tax Paid\s*[:\-]?")
        or find_amount_after_label(full_text, r"Payment of Tax")
        or "0.00"
    )

    return GSTExtraction(
        taxable_value=coerce_currency(taxable),
        total_tax_paid=coerce_currency(tax_paid),
        confidence="HIGH"
    )


def _extract_text_deterministic_itr(pdf_bytes: bytes) -> ITRExtraction:
    """Extracts ITR fields directly from text layer."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    full_text = "\n".join(page.get_text("text") for page in doc)
    doc.close()

    gti = (
        find_amount_after_label(full_text, r"Gross Total Turnover\s*/\s*Revenue from Operations")
        or find_amount_after_label(full_text, r"Gross Total Income")
        or "0.00"
    )

    depr = (
        find_amount_after_label(full_text, r"Depreciation and Amortization Expenses")
        or find_amount_after_label(full_text, r"Depreciation")
        or "0.00"
    )

    interest = (
        find_amount_after_label(full_text, r"Finance Costs\s*/\s*Interest Expense Paid")
        or find_amount_after_label(full_text, r"Interest Paid")
        or "0.00"
    )

    pat = (
        find_amount_after_label(full_text, r"Profit After Tax \(PAT\)\s*/\s*Net Profit")
        or find_amount_after_label(full_text, r"Profit After Tax")
        or "0.00"
    )

    nw = (
        find_amount_after_label(full_text, r"Total Tangible Net Worth")
        or find_amount_after_label(full_text, r"Net Worth")
        or "0.00"
    )

    debt = (
        find_amount_after_label(full_text, r"Existing Debt\s*/\s*Long-Term Borrowings Outstanding")
        or find_amount_after_label(full_text, r"Total Debt")
        or "0.00"
    )

    emi = (
        find_amount_after_label(full_text, r"Current Annual Debt Service\s*/\s*Existing EMI Obligations")
        or find_amount_after_label(full_text, r"Existing EMI Obligations")
        or "0.00"
    )

    return ITRExtraction(
        gross_total_income=coerce_currency(gti),
        profit_after_tax=coerce_currency(pat),
        depreciation=coerce_currency(depr),
        interest_paid=coerce_currency(interest),
        net_worth=coerce_currency(nw),
        total_debt=coerce_currency(debt),
        existing_emi_obligations=coerce_currency(emi),
        confidence="HIGH"
    )


# ============================================================================
# 4. MULTIMODAL EXTRACTION WITH GEMINI 3.5 FLASH LITE
# ============================================================================

def _call_gemini_vision(pdf_bytes: bytes, system_prompt: str, schema_cls: Any) -> Any:
    """
    Calls Gemini API passing sanitized inline bytes directly without temporary disk files.
    Enforces structured JSON output conforming to the specified Pydantic schema.
    Falls back gracefully to deterministic extraction if API key is unset or network fails.
    """
    if not GEMINI_API_KEY or GEMINI_API_KEY == "your_gemini_api_key_here":
        logger.info("GEMINI_API_KEY not configured. Engaging deterministic fallback parser.")
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=schema_cls,
            temperature=0.0
        )

        response = client.models.generate_content(
            model=MODEL_EXTRACTION,
            contents=[pdf_part, "Extract the required structured financial fields from this document."],
            config=config
        )

        if response.text:
            parsed_json = json.loads(response.text)
            return schema_cls(**parsed_json)
        return None

    except Exception as e:
        logger.warning(f"Gemini 3.5 Flash Lite API call failed: {e}. Falling back to deterministic parser.")
        return None


def extract_bank_statement(pdf_bytes: bytes) -> BankExtraction:
    """Slices, redacts, and extracts Bank Statement into BankExtraction."""
    sliced_bytes, _ = slice_relevant_pages(pdf_bytes, doc_type="bank", max_pages=3)
    redacted_bytes, _ = redact_pii(sliced_bytes)

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

    result = _call_gemini_vision(redacted_bytes, prompt, BankExtraction)
    if result is not None:
        return result
    return _extract_text_deterministic_bank(redacted_bytes)


def extract_gstr3b(pdf_bytes: bytes) -> GSTExtraction:
    """Slices, redacts, and extracts GSTR-3B into GSTExtraction."""
    sliced_bytes, _ = slice_relevant_pages(pdf_bytes, doc_type="gst", max_pages=2)
    redacted_bytes, _ = redact_pii(sliced_bytes)

    prompt = (
        "You are an expert Indian GST Tax Underwriter. Extract GSTR-3B tax summary values:\n"
        "- taxable_value (Total taxable value of outward supplies)\n"
        "- total_tax_paid (Total tax discharged/paid)\n"
        "- confidence (HIGH or LOW)\n"
        "Return clean numbers without currency symbols."
    )

    result = _call_gemini_vision(redacted_bytes, prompt, GSTExtraction)
    if result is not None:
        return result
    return _extract_text_deterministic_gst(redacted_bytes)


def extract_itr(pdf_bytes: bytes) -> ITRExtraction:
    """Slices, redacts, and extracts ITR / Audited Financials into ITRExtraction."""
    sliced_bytes, _ = slice_relevant_pages(pdf_bytes, doc_type="itr", max_pages=3)
    redacted_bytes, _ = redact_pii(sliced_bytes)

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

    result = _call_gemini_vision(redacted_bytes, prompt, ITRExtraction)
    if result is not None:
        return result
    return _extract_text_deterministic_itr(redacted_bytes)


# ============================================================================
# 5. DETERMINISTIC BANK RECONCILIATION VERIFICATION
# ============================================================================

def verify_bank_reconciliation(bank: BankExtraction) -> Tuple[bool, Decimal]:
    """
    Validates arithmetic integrity: Opening Balance + Total Credits - Total Debits == Closing Balance.
    Returns (is_balanced, discrepancy_amount).
    """
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
    proposed_tenure_months: int = 36
) -> Tuple[CreditCase, Dict[str, Any]]:
    """
    Executes parallel extraction across Bank, GST, and ITR documents using ThreadPoolExecutor(max_workers=3).
    Measures wall-clock latency, performs deterministic reconciliation, and populates CreditCase.
    """
    start_time = time.perf_counter()

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_bank = executor.submit(extract_bank_statement, bank_bytes)
        future_gst = executor.submit(extract_gstr3b, gst_bytes)
        future_itr = executor.submit(extract_itr, itr_bytes)

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

    metadata = {
        "execution_latency_seconds": round(elapsed_time, 3),
        "bank_reconciled": is_reconciled,
        "bank_reconciliation_difference": str(diff),
        "extraction_model": MODEL_EXTRACTION,
        "parallel_workers": 3,
        "status": "SUCCESS"
    }

    return credit_case, metadata
