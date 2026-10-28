import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=True)
load_dotenv(override=True)

def reload_env():
    load_dotenv(BASE_DIR / ".env", override=True)
    load_dotenv(override=True)

# API Keys (resolved dynamically or at import - supports both GOOGLE_API_KEY and GEMINI_API_KEY)
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            GEMINI_API_KEY = st.secrets.get("GOOGLE_API_KEY") or st.secrets.get("GEMINI_API_KEY")
    except Exception:
        pass

# Model Configuration
MODEL_EXTRACTION = "gemini-3.5-flash-lite"
MODEL_RESEARCH = "gemini-3.8-flash"

# Credit Policy Thresholds
WEAKEST_LINK_THRESHOLD = 40.0
DSCR_REJECT_THRESHOLD = 1.20
BOUNCE_KNOCKOUT_THRESHOLD = 5
MAX_BOUNCES_TOLERANCE = 5
