"""
Test Suite for Stage 1: Design Foundation
Verifies:
1. .streamlit/config.toml exists and conforms to calm institutional design spec.
2. ui/components.py exports required components with HTML escaping.
3. app.py uses Material icons, compact callouts, and no 5-column metric rows.
"""

import re
import html
import sys
from pathlib import Path
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


def test_streamlit_config_toml_palette():
    """Item 13: .streamlit/config.toml exists and configures calm institutional theme."""
    config_file = BASE_DIR / ".streamlit" / "config.toml"
    assert config_file.exists(), ".streamlit/config.toml must exist"

    content = config_file.read_text(encoding="utf-8")
    assert 'primaryColor = "#0F766E"' in content
    assert 'backgroundColor = "#FFFFFF"' in content
    assert 'secondaryBackgroundColor = "#F8FAFC"' in content
    assert 'textColor = "#1F2937"' in content
    assert 'font = "sans serif"' in content


def test_ui_components_escaping_and_contracts():
    """Item 14: ui/components.py provides kpi_card, status_pill, callout, verdict_hero_card, stepper with HTML escaping."""
    from ui.components import (
        kpi_card,
        status_pill,
        callout,
        verdict_hero_card,
        stepper,
    )

    injection_str = "<script>alert('xss')</script> & '\""
    escaped_str = html.escape(injection_str)

    # 1. kpi_card escaping
    kpi_html = kpi_card(
        label=f"Label {injection_str}",
        value=f"Value {injection_str}",
        subtext=f"Sub {injection_str}",
        delta=f"Delta {injection_str}"
    )
    assert "<script>" not in kpi_html
    assert escaped_str in kpi_html

    # 2. status_pill escaping
    pill_html = status_pill(f"Pill {injection_str}", variant="approve")
    assert "<script>" not in pill_html
    assert escaped_str in pill_html

    # 3. callout escaping
    callout_html = callout(
        message=f"Msg {injection_str}",
        title=f"Title {injection_str}",
        variant="warning"
    )
    assert "<script>" not in callout_html
    assert escaped_str in callout_html

    # 4. verdict_hero_card escaping
    hero_html = verdict_hero_card(
        verdict=f"APPROVE {injection_str}",
        score=95.5,
        deciding_rule=f"Rule {injection_str}",
        why=f"Why {injection_str}"
    )
    assert "<script>" not in hero_html
    assert escaped_str in hero_html

    # 5. stepper escaping
    stepper_html = stepper(current_step="Case", steps=["Case", f"Step {injection_str}"])
    assert "<script>" not in stepper_html
    assert escaped_str in stepper_html


def test_app_py_no_five_column_metric_rows():
    """Item 15: app.py must have max 4 columns per row for KPI metrics (no st.columns(5))."""
    app_text = (BASE_DIR / "app.py").read_text(encoding="utf-8")
    assert "st.columns(5)" not in app_text, "5-column metric rows must be replaced with max 4 columns per row"


def test_app_py_no_emojis_in_tabs_or_titles():
    """Item 15: Common emojis replaced with Material icons or clean labels in app.py."""
    app_text = (BASE_DIR / "app.py").read_text(encoding="utf-8")
    
    # Emojis that were previously in tabs, buttons, or headers
    disallowed_emojis = ["🏢", "🤖", "⚖️", "📑", "📜", "🚀", "🔒", "🔄", "🔎", "🛡️", "⚡", "💡", "💰", "📂"]
    for emoji in disallowed_emojis:
        assert emoji not in app_text, f"Emoji {emoji} found in app.py; should be replaced with Material icon"


def test_ui_components_no_commonmark_code_block_indents():
    """Verifies that all generated component HTML has zero lines starting with 4+ spaces,
    preventing CommonMark indented code block leakage."""
    from ui.components import verdict_hero_card, callout, kpi_card

    for v in ["APPROVE", "CONDITIONAL", "REJECT"]:
        for overridden in [False, True]:
            html = verdict_hero_card(
                v, 95.0, "Rule test", "Explanation",
                overridden=overridden,
                baseline_verdict="APPROVE",
                approver="Officer"
            )
            for line in html.splitlines():
                assert not line.startswith("    "), f"Line starts with 4+ spaces: {repr(line)}"

    for title in [None, "Title"]:
        for line in callout("Message", title=title).splitlines():
            assert not line.startswith("    "), f"callout line starts with 4+ spaces: {repr(line)}"

    for delta in [None, "Delta"]:
        for line in kpi_card("Label", "Value", delta=delta).splitlines():
            assert not line.startswith("    "), f"kpi_card line starts with 4+ spaces: {repr(line)}"
