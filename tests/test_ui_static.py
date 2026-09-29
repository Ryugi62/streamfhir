"""SPEC §8 UI criteria that can be checked statically (1, 5, 7, 10 + accessibility hooks)."""
import os
import re

from tests.conftest import ROOT

HTML = open(os.path.join(ROOT, "streamfhir", "infrastructure", "static", "index.html"), encoding="utf-8").read()


def test_mobile_viewport_and_no_external_resources():
    assert '<meta name="viewport"' in HTML
    assert not re.search(r'<(script|link|img)[^>]+(src|href)="https?://', HTML)


def test_single_fixed_primary_cta_at_least_52px():
    assert HTML.count('id="cta"') == 1
    assert re.search(r"\.cta\{position:fixed", HTML)
    assert int(re.search(r"\.cta button\{[^}]*height:(\d+)px", HTML).group(1)) >= 52


def test_evidence_is_folded_and_status_is_announced():
    assert "<details>" in HTML and "<details open" not in HTML
    assert 'aria-live="polite"' in HTML and 'role="status"' in HTML
    assert '<nav aria-label="Main">' in HTML


def test_ac38_real_datasets_lead_with_ecological_condition_not_hazard():
    """Real agency data has no citizen hazard observations: the UI leads with condition and never says 'not corroborated' for a lab value."""
    import os
    html = open(os.path.join(os.path.dirname(__file__), "..", "streamfhir", "infrastructure", "static", "index.html"), encoding="utf-8").read()
    assert "real monitoring stations" in html and "above a nutrient screening value" in html
    assert "Agency lab value" in html and "Agency samplings (lab chemistry)" in html
    assert "coloured by ecological condition" in html
