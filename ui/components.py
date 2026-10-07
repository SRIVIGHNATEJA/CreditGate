"""
CreditGate UI Components Library
Institutional Design System: Calm Institutional Theme
Palette:
  - Headings: Navy #0B1F3A
  - Primary Action: Teal #0F766E
  - Surface: #F8FAFC
  - Border: #E2E8F0
  - Text: #1F2937
  - Muted: #64748B
  - Decisions only:
      APPROVE #15803D
      CONDITIONAL #B45309
      REJECT #B91C1C
"""

import html
from typing import Optional, List, Dict, Any
import streamlit as st

# Color Palette Constants
COLOR_NAVY = "#0B1F3A"
COLOR_TEAL = "#0F766E"
COLOR_SURFACE = "#F8FAFC"
COLOR_BORDER = "#E2E8F0"
COLOR_TEXT = "#1F2937"
COLOR_TEXT_MUTED = "#64748B"

# Verdict Colors (Used strictly for decisions)
COLOR_APPROVE = "#15803D"
COLOR_APPROVE_BG = "#F0FDF4"
COLOR_APPROVE_BORDER = "#BBF7D0"

COLOR_CONDITIONAL = "#B45309"
COLOR_CONDITIONAL_BG = "#FFFBEB"
COLOR_CONDITIONAL_BORDER = "#FDE68A"

COLOR_REJECT = "#B91C1C"
COLOR_REJECT_BG = "#FEF2F2"
COLOR_REJECT_BORDER = "#FECACA"

COLOR_NEUTRAL_BG = "#F1F5F9"
COLOR_NEUTRAL_TEXT = "#475569"


def _escape(val: Any) -> str:
    """Safely escapes text to prevent injection or broken HTML tags."""
    if val is None:
        return ""
    return html.escape(str(val))


# ============================================================================
# 1. STATUS PILL
# ============================================================================

def status_pill(text: str, variant: str = "neutral") -> str:
    """
    Renders a compact, high-contrast status pill.
    Variants: 'approve', 'conditional', 'reject', 'info', 'teal', 'neutral'.
    """
    clean_text = _escape(text)
    variant = variant.lower()

    if variant in ("approve", "success"):
        bg = COLOR_APPROVE_BG
        fg = COLOR_APPROVE
        border = COLOR_APPROVE_BORDER
    elif variant in ("conditional", "warning"):
        bg = COLOR_CONDITIONAL_BG
        fg = COLOR_CONDITIONAL
        border = COLOR_CONDITIONAL_BORDER
    elif variant in ("reject", "danger", "error"):
        bg = COLOR_REJECT_BG
        fg = COLOR_REJECT
        border = COLOR_REJECT_BORDER
    elif variant in ("info", "teal", "primary"):
        bg = "#CCFBF1"
        fg = COLOR_TEAL
        border = "#99F6E4"
    else:
        bg = COLOR_NEUTRAL_BG
        fg = COLOR_NEUTRAL_TEXT
        border = COLOR_BORDER

    return f"""<span style="
        display: inline-flex;
        align-items: center;
        padding: 2px 10px;
        font-size: 11px;
        font-weight: 600;
        border-radius: 9999px;
        background-color: {bg};
        color: {fg};
        border: 1px solid {border};
        line-height: 1.4;
        white-space: nowrap;
        letter-spacing: 0.02em;
    ">{clean_text}</span>"""


# ============================================================================
# 2. KPI CARD
# ============================================================================

def kpi_card(
    label: str,
    value: str,
    subtext: Optional[str] = None,
    delta: Optional[str] = None,
    delta_color: str = "neutral",
    tooltip: Optional[str] = None
) -> str:
    """
    Renders a standardized, non-wrapping KPI card conforming to the Calm Institutional palette.
    Guaranteed no label wrapping at 1366px viewport width.
    """
    clean_label = _escape(label)
    clean_value = _escape(value)
    clean_subtext = _escape(subtext) if subtext else ""
    clean_delta = _escape(delta) if delta else ""
    title_attr = f'title="{_escape(tooltip)}"' if tooltip else f'title="{clean_label}"'

    d_color = COLOR_TEXT_MUTED
    if delta_color in ("normal", "success", "approve"):
        d_color = COLOR_APPROVE
    elif delta_color in ("inverse", "danger", "reject", "error"):
        d_color = COLOR_REJECT
    elif delta_color in ("warning", "conditional"):
        d_color = COLOR_CONDITIONAL
    elif delta_color in ("teal", "primary"):
        d_color = COLOR_TEAL

    delta_html = ""
    if clean_delta:
        delta_html = f"""<div style="font-size: 11px; font-weight: 600; color: {d_color}; margin-top: 4px; white-space: nowrap;">{clean_delta}</div>"""
    elif clean_subtext:
        delta_html = f"""<div style="font-size: 11px; font-weight: 500; color: {COLOR_TEXT_MUTED}; margin-top: 4px; white-space: nowrap;">{clean_subtext}</div>"""

    return f"""<div {title_attr} style="
        background-color: {COLOR_SURFACE};
        border: 1px solid {COLOR_BORDER};
        border-radius: 8px;
        padding: 12px 14px;
        box-sizing: border-box;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        min-height: 86px;
        width: 100%;
        margin-bottom: 8px;
    ">
        <div style="
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: {COLOR_TEXT_MUTED};
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            margin-bottom: 4px;
        ">{clean_label}</div>
        <div style="
            font-size: 20px;
            font-weight: 700;
            color: {COLOR_NAVY};
            line-height: 1.2;
            letter-spacing: -0.01em;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        ">{clean_value}</div>
        {delta_html}
    </div>"""


def render_kpi_card(
    label: str,
    value: str,
    subtext: Optional[str] = None,
    delta: Optional[str] = None,
    delta_color: str = "neutral",
    tooltip: Optional[str] = None
):
    """Convenience helper to render KPI card directly into Streamlit."""
    st.markdown(
        kpi_card(label, value, subtext=subtext, delta=delta, delta_color=delta_color, tooltip=tooltip),
        unsafe_allow_html=True
    )


# ============================================================================
# 3. CALLOUT (COMPACT ALERT BOX)
# ============================================================================

def callout(
    message: str,
    title: Optional[str] = None,
    variant: str = "info"
) -> str:
    """
    Renders a compact, institutional callout box replacing full-width st.success/warning/error.
    Variants: 'info', 'success', 'warning', 'error'.
    """
    clean_msg = _escape(message)
    clean_title = _escape(title) if title else ""
    variant = variant.lower()

    if variant in ("success", "approve"):
        accent = COLOR_APPROVE
        bg = COLOR_APPROVE_BG
        border = COLOR_APPROVE_BORDER
        icon_svg = """<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#15803D" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>"""
    elif variant in ("warning", "conditional"):
        accent = COLOR_CONDITIONAL
        bg = COLOR_CONDITIONAL_BG
        border = COLOR_CONDITIONAL_BORDER
        icon_svg = """<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#B45309" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>"""
    elif variant in ("error", "danger", "reject"):
        accent = COLOR_REJECT
        bg = COLOR_REJECT_BG
        border = COLOR_REJECT_BORDER
        icon_svg = """<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#B91C1C" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>"""
    else:  # info / default
        accent = COLOR_TEAL
        bg = "#F0FDFA"
        border = "#CCFBF1"
        icon_svg = """<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0F766E" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>"""

    title_html = f"""<div style="font-weight: 700; color: {COLOR_NAVY}; margin-bottom: 2px;">{clean_title}</div>""" if clean_title else ""

    return f"""<div style="
        background-color: {bg};
        border: 1px solid {border};
        border-left: 4px solid {accent};
        border-radius: 6px;
        padding: 10px 14px;
        margin: 8px 0;
        display: flex;
        align-items: flex-start;
        gap: 12px;
        box-sizing: border-box;
    ">
        <div style="margin-top: 1px;">{icon_svg}</div>
        <div style="flex-grow: 1; font-size: 13px; color: {COLOR_TEXT}; line-height: 1.45;">
            {title_html}
            <div>{clean_msg}</div>
        </div>
    </div>"""


def render_callout(
    message: str,
    title: Optional[str] = None,
    variant: str = "info"
):
    """Convenience helper to render callout directly into Streamlit."""
    st.markdown(callout(message, title=title, variant=variant), unsafe_allow_html=True)


# ============================================================================
# 4. VERDICT HERO CARD
# ============================================================================

def verdict_hero_card(
    verdict: str,
    score: float,
    deciding_rule: Optional[str] = None,
    why: Optional[str] = None,
    overridden: bool = False,
    baseline_verdict: Optional[str] = None,
    approver: Optional[str] = None
) -> str:
    """
    Renders a prominent, authoritative underwriting decision hero card.
    Strictly uses decision palette: APPROVE (#15803D), CONDITIONAL (#B45309), REJECT (#B91C1C).
    """
    v_clean = _escape(verdict).upper()
    rule_clean = _escape(deciding_rule) if deciding_rule else "Algorithmic Underwriting Baseline"
    why_clean = _escape(why) if why else ""
    baseline_clean = _escape(baseline_verdict) if baseline_verdict else ""
    approver_clean = _escape(approver) if approver else "Credit Committee Officer"

    if "APPROVE" in v_clean:
        accent = COLOR_APPROVE
        bg = COLOR_APPROVE_BG
        border = COLOR_APPROVE_BORDER
        badge_variant = "approve"
    elif "CONDITIONAL" in v_clean:
        accent = COLOR_CONDITIONAL
        bg = COLOR_CONDITIONAL_BG
        border = COLOR_CONDITIONAL_BORDER
        badge_variant = "conditional"
    elif "REJECT" in v_clean:
        accent = COLOR_REJECT
        bg = COLOR_REJECT_BG
        border = COLOR_REJECT_BORDER
        badge_variant = "reject"
    else:
        accent = COLOR_NEUTRAL_TEXT
        bg = COLOR_SURFACE
        border = COLOR_BORDER
        badge_variant = "neutral"

    override_banner = ""
    if overridden:
        override_banner = f"""<div style="
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background-color: #FEF3C7;
            color: #92400E;
            border: 1px solid #FDE68A;
            border-radius: 4px;
            padding: 3px 8px;
            font-size: 11px;
            font-weight: 600;
            margin-bottom: 8px;
        ">
            <span>⚠️ Overridden by officer</span>
            <span style="color: #64748B;">• Baseline: {baseline_clean} • Approver: {approver_clean}</span>
        </div>"""

    score_pill = f"""<div style="
        font-size: 18px;
        font-weight: 700;
        color: {COLOR_NAVY};
        background: #FFFFFF;
        border: 1px solid {COLOR_BORDER};
        padding: 4px 12px;
        border-radius: 6px;
        letter-spacing: -0.01em;
    ">Composite Score: {score:.1f} / 100.0</div>"""

    return f"""<div style="
        background-color: {bg};
        border: 1px solid {border};
        border-left: 6px solid {accent};
        border-radius: 8px;
        padding: 18px 22px;
        margin: 12px 0 16px 0;
        box-sizing: border-box;
    ">
        {override_banner}
        <div style="
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 8px;
        ">
            <div>
                <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: {COLOR_TEXT_MUTED};">
                    Final Underwriting Assessment
                </div>
                <div style="font-size: 26px; font-weight: 800; color: {accent}; letter-spacing: -0.02em; line-height: 1.2;">
                    {v_clean}
                </div>
            </div>
            {score_pill}
        </div>
        <div style="border-top: 1px solid {border}; padding-top: 10px; margin-top: 6px; display: flex; flex-direction: column; gap: 4px;">
            <div style="font-size: 13px; font-weight: 600; color: {COLOR_NAVY};">
                Deciding Rule: <span style="font-weight: 500; color: {COLOR_TEXT};">{rule_clean}</span>
            </div>
            {f'<div style="font-size: 12px; color: {COLOR_TEXT_MUTED}; line-height: 1.4;">{why_clean}</div>' if why_clean else ''}
        </div>
    </div>"""


def render_verdict_hero_card(
    verdict: str,
    score: float,
    deciding_rule: Optional[str] = None,
    why: Optional[str] = None,
    overridden: bool = False,
    baseline_verdict: Optional[str] = None,
    approver: Optional[str] = None
):
    """Convenience helper to render verdict hero card directly into Streamlit."""
    st.markdown(
        verdict_hero_card(
            verdict,
            score,
            deciding_rule=deciding_rule,
            why=why,
            overridden=overridden,
            baseline_verdict=baseline_verdict,
            approver=approver
        ),
        unsafe_allow_html=True
    )


# ============================================================================
# 5. STEPPER
# ============================================================================

def stepper(current_step: str = "Case", steps: Optional[List[str]] = None) -> str:
    """
    Renders a persistent horizontal stepper for workflow tracking:
    Case > Evidence > Decision > CAM & Audit
    """
    if steps is None:
        steps = ["Case", "Evidence", "Decision", "CAM & Audit"]

    curr_idx = 0
    clean_curr = current_step.strip().lower()
    for idx, s in enumerate(steps):
        if s.lower() in clean_curr or clean_curr in s.lower():
            curr_idx = idx
            break

    # Real state checks per B5
    is_extracted = (st.session_state.get("locked_case") is not None or st.session_state.get("extracted_case") is not None)
    is_locked = (st.session_state.get("locked_case") is not None)
    is_scored = (st.session_state.get("score_result") is not None)
    is_cam = (st.session_state.get("cam_markdown") is not None)

    items_html = []
    for idx, step_name in enumerate(steps):
        clean_name = _escape(step_name)
        step_num = idx + 1
        is_active = (step_name.lower() in clean_curr or clean_curr in step_name.lower())

        # Determine real completion state
        if idx == 0:
            step_completed = True
        elif idx == 1:
            step_completed = is_locked
        elif idx == 2:
            step_completed = is_scored
        elif idx == 3:
            step_completed = is_cam
        else:
            step_completed = False

        if is_active:
            # Active step styling
            circle_content = "✓" if step_completed else str(step_num)
            circle = f"""<span style="
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 22px;
                height: 22px;
                border-radius: 50%;
                background-color: {COLOR_NAVY};
                color: #FFFFFF;
                font-size: 11px;
                font-weight: 700;
                margin-right: 8px;
            ">{circle_content}</span>"""
            label_style = f"font-size: 13px; font-weight: 700; color: {COLOR_NAVY}; border-bottom: 2px solid {COLOR_TEAL}; padding-bottom: 2px;"
        elif step_completed:
            # Completed inactive step
            circle = f"""<span style="
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 22px;
                height: 22px;
                border-radius: 50%;
                background-color: {COLOR_TEAL};
                color: #FFFFFF;
                font-size: 11px;
                font-weight: 700;
                margin-right: 8px;
            ">✓</span>"""
            label_style = f"font-size: 13px; font-weight: 600; color: {COLOR_NAVY};"
        else:
            # Incomplete inactive step
            circle = f"""<span style="
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 22px;
                height: 22px;
                border-radius: 50%;
                background-color: #E2E8F0;
                color: {COLOR_TEXT_MUTED};
                font-size: 11px;
                font-weight: 600;
                margin-right: 8px;
            ">{step_num}</span>"""
            label_style = f"font-size: 13px; font-weight: 500; color: {COLOR_TEXT_MUTED};"

        item = f"""<div style="display: flex; align-items: center;">
            {circle}
            <span style="{label_style}">{clean_name}</span>
        </div>"""
        items_html.append(item)

    chevron = f"""<span style="color: #94A3B8; font-size: 16px; margin: 0 10px; user-select: none;">›</span>"""
    content = chevron.join(items_html)

    return f"""<div style="
        background-color: #FFFFFF;
        border: 1px solid {COLOR_BORDER};
        border-radius: 8px;
        padding: 10px 18px;
        margin: 0 0 18px 0;
        display: flex;
        align-items: center;
        justify-content: space-between;
        box-sizing: border-box;
        overflow-x: auto;
    ">
        {content}
    </div>"""


def render_stepper(current_step: str = "Case", steps: Optional[List[str]] = None):
    """Convenience helper to render stepper directly into Streamlit."""
    st.markdown(stepper(current_step, steps=steps), unsafe_allow_html=True)
