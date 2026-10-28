"""CreditGate UI Package"""
from ui.components import (
    kpi_card,
    render_kpi_card,
    status_pill,
    callout,
    render_callout,
    verdict_hero_card,
    render_verdict_hero_card,
    stepper,
    render_stepper,
    _clean_html,
    _render_html,
    COLOR_NAVY,
    COLOR_TEAL,
    COLOR_SURFACE,
    COLOR_BORDER,
    COLOR_TEXT,
    COLOR_APPROVE,
    COLOR_CONDITIONAL,
    COLOR_REJECT,
)

from ui.state import (
    select_and_reset_cohort,
    init_session_state,
    render_sidebar,
    get_app_context,
    record_event,
    reset_facility_to_defaults,
    get_default_quorum_governance,
)
