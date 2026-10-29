import streamlit as st
from ui.state import init_session_state, render_sidebar
from ui.components import render_stepper

# ----------------------------------------------------------------------------
# 1. PAGE CONFIGURATION & INSTITUTIONAL STYLING (CALM INSTITUTIONAL PALETTE)
# ----------------------------------------------------------------------------

st.set_page_config(
    page_title="CreditGate | AI Underwriting Copilot",
    page_icon=":material/shield:",
    layout="wide",
    initial_sidebar_state="auto",
)

st.markdown("""
<style>
    /* Executive Viewport & Top Padding Optimization */
    .block-container {
        padding-top: 1.75rem !important;
        padding-bottom: 3rem !important;
    }
    header[data-testid=stHeader] {
        height: 2.25rem !important;
        background: transparent !important;
    }

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

    /* Institutional Sidebar Ribbon Aesthetics */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #F8FAFC 0%, #F1F5F9 100%) !important;
        border-right: 1px solid #CBD5E1 !important;
        box-shadow: 2px 0 16px rgba(15, 23, 42, 0.05) !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stSidebarHeader"] {
        padding-bottom: 0px !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] {
        padding-top: 6px !important;
        margin-bottom: 12px !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] a {
        border-radius: 6px !important;
        margin: 3px 4px !important;
        padding: 8px 14px !important;
        font-size: 13.5px !important;
        font-weight: 600 !important;
        color: #334155 !important;
        transition: all 0.15s ease-in-out !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] a:hover {
        background-color: #EEF2F6 !important;
        color: #0F766E !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] a[aria-current="page"] {
        background: linear-gradient(90deg, #0B1F3A 0%, #162C4E 100%) !important;
        color: #FFFFFF !important;
        font-weight: 700 !important;
        border-left: 3px solid #0F766E !important;
        box-shadow: 0 2px 8px rgba(11, 31, 58, 0.25) !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stSidebarNav"] a[aria-current="page"] span {
        color: #FFFFFF !important;
    }
    section[data-testid="stSidebar"] hr {
        margin: 12px 0 !important;
        border-top: 1px solid #CBD5E1 !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stVerticalBlockBorderWrapper"] {
        background: #FFFFFF !important;
        border: 1px solid #CBD5E1 !important;
        border-radius: 8px !important;
        box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04) !important;
        margin-bottom: 12px !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stVerticalBlockBorderWrapper"] > div {
        padding: 10px 14px 12px 14px !important;
    }
    section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"] {
        border-radius: 6px !important;
        font-weight: 700 !important;
        font-size: 13px !important;
        color: #334155 !important;
        border: 1px solid #CBD5E1 !important;
        background: #FFFFFF !important;
        padding: 8px 14px !important;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04) !important;
        transition: all 0.15s ease-in-out !important;
    }
    section[data-testid="stSidebar"] button[data-testid="stBaseButton-secondary"]:hover {
        background: #F1F5F9 !important;
        border-color: #0F766E !important;
        color: #0F766E !important;
    }

    /* -------------------------------------------------------------
       RESPONSIVE VIEWPORT & MOBILE ORIENTATION ADVISORY SYSTEM
       ------------------------------------------------------------- */
    
    /* Base Mobile Advisory Banner Styling (Hidden on Desktop by default) */
    .mobile-orientation-banner {
        display: none;
        align-items: center;
        justify-content: space-between;
        background: #0B1F3A;
        border: 1px solid #0F766E;
        border-radius: 8px;
        padding: 10px 14px;
        margin-bottom: 14px;
        color: #F8FAFC;
        font-size: 0.82rem;
        line-height: 1.35;
        box-shadow: 0 4px 12px rgba(11, 31, 58, 0.25);
    }
    .mobile-orientation-banner .rotate-icon {
        display: inline-block;
        font-size: 1.3rem;
        animation: rotate-hint 3s ease-in-out infinite;
    }
    .mobile-orientation-banner .dismiss-btn {
        cursor: pointer;
        padding: 4px 8px;
        font-size: 1.1rem;
        font-weight: 700;
        color: #94A3B8;
        border-radius: 4px;
        user-select: none;
        -webkit-user-select: none;
        line-height: 1;
        transition: color 0.15s ease-in-out, background-color 0.15s ease-in-out;
    }
    .mobile-orientation-banner .dismiss-btn:hover {
        color: #FFFFFF;
        background-color: rgba(255, 255, 255, 0.1);
    }
    @keyframes rotate-hint {
        0%, 100% { transform: rotate(0deg); }
        35%, 65% { transform: rotate(-90deg); }
    }

    /* 1. PORTRAIT MOBILE (< 768px & Portrait): Show Advisory */
    @media screen and (max-width: 768px) and (orientation: portrait) {
        .mobile-orientation-banner {
            display: flex !important;
        }
        /* Pure CSS Checkbox Dismissal Hack (Higher cascade priority) */
        #dismiss-orientation-check:checked + .mobile-orientation-banner {
            display: none !important;
        }
        /* Restrict expanded sidebar content from exceeding 85% of screen width */
        div[data-testid="stSidebarContent"] {
            max-width: 85vw !important;
        }
        .block-container {
            padding-left: 1rem !important;
            padding-right: 1rem !important;
            padding-top: 1rem !important;
        }
    }

    /* 2. LANDSCAPE MOBILE (Landscape & height <= 520px): Maximize Vertical & Horizontal Real Estate */
    @media screen and (orientation: landscape) and (max-height: 520px) {
        .mobile-orientation-banner {
            display: none !important;
        }
        /* Preserve sidebar collapsed hamburger button while minimizing vertical footprint */
        header[data-testid="stHeader"] {
            background: transparent !important;
            height: 1.5rem !important;
        }
        .block-container {
            padding-top: 0.5rem !important;
            padding-bottom: 1.5rem !important;
            padding-left: 1rem !important;
            padding-right: 1rem !important;
        }
    }

    /* 3. DESKTOP & LAPTOPS (> 768px or height > 520px): Hide Banner */
    @media screen and (min-width: 769px) and (min-height: 521px) {
        .mobile-orientation-banner {
            display: none !important;
        }
    }

    /* Native touch momentum scrolling for financial tables */
    [data-testid="stDataFrame"], [data-testid="stTable"] {
        -webkit-overflow-scrolling: touch;
    }
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# 2. SESSION STATE INITIALIZATION & SIDEBAR RENDERING
# ----------------------------------------------------------------------------

init_session_state()
ctx = render_sidebar()

# ----------------------------------------------------------------------------
# 3. MULTI-PAGE ROUTING & PERSISTENT STEPPER
# ----------------------------------------------------------------------------

pages = [
    st.Page("app_pages/case.py", title="Case", icon=":material/domain:"),
    st.Page("app_pages/evidence.py", title="Evidence", icon=":material/fact_check:"),
    st.Page("app_pages/decision.py", title="Decision", icon=":material/balance:"),
    st.Page("app_pages/cam_audit.py", title="CAM & Audit", icon=":material/description:"),
]
pg = st.navigation(pages)

st.markdown("""
<input type="checkbox" id="dismiss-orientation-check" style="display: none;">
<div class="mobile-orientation-banner">
    <div style="display: flex; align-items: center; gap: 10px;">
        <span class="rotate-icon">📱</span>
        <div>
            <strong style="color: #F8FAFC;">Institutional Underwriting Terminal</strong><br>
            <span style="color: #94A3B8;">Rotate device to <strong>Landscape</strong> for optimal multi-column cockpit view.</span>
        </div>
    </div>
    <label for="dismiss-orientation-check" class="dismiss-btn" title="Dismiss">✕</label>
</div>
""", unsafe_allow_html=True)

st.markdown('<div class="main-title">CreditGate: AI MSME Underwriting Copilot</div>', unsafe_allow_html=True)
# Standard 4-page navigation pipeline synchronized with current router destination:
# 1. Case | 2. Evidence | 3. Decision | 4. CAM & Audit
active_workflow_stage = getattr(pg, "title", None) or st.session_state.get("current_page", "Case")
st.session_state.current_page = active_workflow_stage
render_stepper(active_workflow_stage)

pg.run()
