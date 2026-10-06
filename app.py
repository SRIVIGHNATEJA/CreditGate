import streamlit as st
from ui.state import init_session_state, render_sidebar
from ui.components import render_stepper

# ----------------------------------------------------------------------------
# 1. PAGE CONFIGURATION & ENTERPRISE STYLING (CALM INSTITUTIONAL PALETTE)
# ----------------------------------------------------------------------------

st.set_page_config(
    page_title="CreditGate | AI Underwriting Copilot",
    page_icon=":material/shield:",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    /* Typography & Core Palette */
    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        color: #1F2937;
    }
    h1, h2, h3, h4, h5, h6 {
        color: #0B1F3A !important;
        font-weight: 700 !important;
        letter-spacing: -0.01em;
    }
    .main-title {
        font-size: 24px;
        font-weight: 800;
        color: #0B1F3A;
        margin-bottom: 2px;
        letter-spacing: -0.02em;
    }
    .sub-title {
        font-size: 13px;
        color: #64748B;
        margin-bottom: 16px;
    }
    
    /* Institutional Form & Card Containers */
    .stButton > button[kind="primary"] {
        background-color: #0F766E !important;
        border-color: #0F766E !important;
        color: #FFFFFF !important;
        font-weight: 600;
        border-radius: 6px;
    }
    .stButton > button[kind="secondary"] {
        background-color: #FFFFFF !important;
        border-color: #E2E8F0 !important;
        color: #0F766E !important;
        border-radius: 6px;
    }
    .gate-card-hold {
        background-color: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-left: 4px solid #B91C1C;
        border-radius: 8px;
        padding: 20px 24px;
        text-align: left;
        margin-top: 16px;
    }
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# 2. SESSION STATE INITIALIZATION & SIDEBAR RENDERING
# ----------------------------------------------------------------------------

init_session_state()
ctx = render_sidebar()

# ----------------------------------------------------------------------------
# 3. HEADER & PERSISTENT STEPPER
# ----------------------------------------------------------------------------

st.markdown('<div class="main-title">CreditGate: AI MSME Underwriting Copilot</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">1. Case &nbsp;|&nbsp; 2. Evidence &nbsp;|&nbsp; 3. Decision &nbsp;|&nbsp; 4. CAM & Audit</div>', unsafe_allow_html=True)

active_workflow_stage = st.session_state.get("current_page", "Case")
render_stepper(active_workflow_stage)

# ----------------------------------------------------------------------------
# 4. MULTI-PAGE ROUTING VIA st.navigation
# ----------------------------------------------------------------------------

pages = [
    st.Page("app_pages/case.py", title="Case", icon=":material/domain:"),
    st.Page("app_pages/evidence.py", title="Evidence", icon=":material/fact_check:"),
    st.Page("app_pages/decision.py", title="Decision", icon=":material/balance:"),
    st.Page("app_pages/cam_audit.py", title="CAM & Audit", icon=":material/description:"),
]
pg = st.navigation(pages)
pg.run()
