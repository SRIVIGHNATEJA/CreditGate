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
        font-feature-settings: "cv02", "cv03", "cv04", "cv11";
    }
    h1, h2, h3, h4, h5, h6 {
        color: #0B1F3A !important;
        font-weight: 700 !important;
        letter-spacing: -0.015em;
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

    /* Tabular Numerals for Financial Data Alignment */
    div[data-testid="stMetricValue"], [data-testid="stDataFrame"], table {
        font-feature-settings: "tnum" 1;
    }

    /* Modern Slim Institutional Scrollbars */
    ::-webkit-scrollbar {
        width: 6px;
        height: 6px;
    }
    ::-webkit-scrollbar-track {
        background: #F1F5F9;
    }
    ::-webkit-scrollbar-thumb {
        background: #CBD5E1;
        border-radius: 3px;
    }
    ::-webkit-scrollbar-thumb:hover {
        background: #0F766E;
    }
    
    /* Institutional Form & Card Containers */
    .stButton > button[kind="primary"] {
        background-color: #0F766E !important;
        border-color: #0F766E !important;
        color: #FFFFFF !important;
        font-weight: 600;
        border-radius: 6px;
        box-shadow: 0 1px 2px 0 rgba(15, 118, 110, 0.2);
        transition: all 0.15s ease-in-out;
    }
    .stButton > button[kind="primary"]:hover {
        background-color: #0D655E !important;
        border-color: #0D655E !important;
        box-shadow: 0 2px 4px 0 rgba(15, 118, 110, 0.3);
    }
    .stButton > button[kind="primary"]:focus {
        outline: 2px solid #0F766E !important;
        outline-offset: 2px !important;
    }
    .stButton > button[kind="secondary"] {
        background-color: #FFFFFF !important;
        border-color: #CBD5E1 !important;
        color: #0F766E !important;
        font-weight: 600;
        border-radius: 6px;
        transition: all 0.15s ease-in-out;
    }
    .stButton > button[kind="secondary"]:hover {
        background-color: #F8FAFC !important;
        border-color: #0F766E !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-color: #E2E8F0 !important;
        box-shadow: 0 1px 3px 0 rgba(15, 23, 42, 0.04);
        border-radius: 8px;
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
    code, pre {
        background-color: #F8FAFC !important;
        border: 1px solid #E2E8F0 !important;
        border-radius: 4px !important;
        color: #0B1F3A !important;
    }
    /* Toast Notification Styling */
    div[data-testid="stToast"] {
        background-color: #0B1F3A !important;
        border: 1px solid #0F766E !important;
        border-radius: 6px !important;
        color: #F8FAFC !important;
        box-shadow: 0 4px 12px rgba(11, 31, 58, 0.15) !important;
    }
    div[data-testid="stToast"] p {
        color: #F8FAFC !important;
    }
    /* Horizontal Rule */
    hr {
        border: none !important;
        border-top: 1px solid #E2E8F0 !important;
        margin: 16px 0 !important;
    }
    /* Data Editor High Contrast */
    [data-testid="stDataEditor"] {
        border-radius: 6px;
    }
    /* Radio and Checkbox Accents */
    input[type="radio"]:checked, input[type="checkbox"]:checked {
        accent-color: #0F766E !important;
    }
    /* Number Input Stepper Controls */
    div[data-testid="stNumberInputStepDown"], div[data-testid="stNumberInputStepUp"] {
        border-color: #E2E8F0 !important;
    }
    div[data-testid="stNumberInputStepDown"]:hover, div[data-testid="stNumberInputStepUp"]:hover {
        background-color: #F1F5F9 !important;
        color: #0F766E !important;
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
