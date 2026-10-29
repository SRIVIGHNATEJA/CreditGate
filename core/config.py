import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=True)
load_dotenv(override=True)

def reload_env():
    load_dotenv(BASE_DIR / ".env", override=True)
    load_dotenv(override=True)

def get_gemini_api_key() -> Optional[str]:
    """
    Authoritative resolution of Google Gemini API Key.
    Checks environment variables first, then Streamlit Cloud secrets (st.secrets).
    Guarantees consistent resolution across local development, Docker, and Streamlit Cloud.
    """
    key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key or key == "your_gemini_api_key_here":
        try:
            import streamlit as st
            if hasattr(st, "secrets"):
                key = st.secrets.get("GOOGLE_API_KEY") or st.secrets.get("GEMINI_API_KEY")
        except Exception:
            pass
    if key and str(key).strip() and str(key).strip() != "your_gemini_api_key_here":
        clean_key = str(key).strip()
        os.environ.setdefault("GOOGLE_API_KEY", clean_key)
        return clean_key
    return None

# API Keys (resolved dynamically or at import - supports both GOOGLE_API_KEY and GEMINI_API_KEY)
GEMINI_API_KEY = get_gemini_api_key()

# Model Configuration (defaults to Google AI Studio models for CreditGateProduction)
MODEL_EXTRACTION = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
MODEL_RESEARCH = os.getenv("GEMINI_RESEARCH_MODEL", "gemini-3.8-flash")

# Credit Policy Thresholds
WEAKEST_LINK_THRESHOLD = 40.0
DSCR_REJECT_THRESHOLD = 1.20
BOUNCE_KNOCKOUT_THRESHOLD = 5
MAX_BOUNCES_TOLERANCE = 5
