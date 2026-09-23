"""
Revenue bar chart tests -- every bar must carry a real, exact figure in the
markup itself, not just a hover-only tooltip. Run: python tests/test_bar_chart.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop("FINNHUB_API_KEY", None)
from web.app import bar_chart                                # noqa: E402
os.environ.pop("FINNHUB_API_KEY", None)


def test_every_bar_carries_the_exact_unrounded_figure():
    """The whole point: clicking a bar must show the real number, not the
    abbreviated one already visible above it."""
    svg = bar_chart([("FY2023", 365822000000.0), ("FY2024", 391035000000.0)])
    assert 'data-exact="365,822,000,000"' in svg
    assert 'data-exact="391,035,000,000"' in svg
    print("  OK  exact, comma-formatted, unrounded figures are in the markup")


def test_abbreviated_value_is_visible_without_any_interaction():
    """A value label must render as real SVG <text>, not only inside a
    <title> tooltip that needs a hover to appear."""
    svg = bar_chart([("FY2024", 391035000000.0)])
    assert '<text' in svg and 'class="val-lbl"' in svg
    assert "391.04B" in svg
    print("  OK  the abbreviated figure is a real visible label, not hover-only")


def test_bars_are_focusable_and_carry_the_year():
    svg = bar_chart([("FY2024", 391035000000.0)])
    assert 'tabindex="0"' in svg, "a bar must be keyboard-reachable, not mouse/touch only"
    assert 'data-label="FY2024"' in svg
    print("  OK  bars are keyboard-focusable and labelled with their year")


def test_none_values_are_skipped_not_rendered_as_zero_height_bars():
    svg = bar_chart([("FY2022", None), ("FY2023", 100.0)])
    assert svg.count('class="bar chart-bar"') == 1, "a null year must not become a fake 0 bar"
    print("  OK  a null revenue year is omitted, not drawn as a misleading zero bar")


def test_empty_input_renders_nothing_not_a_broken_empty_svg():
    assert bar_chart([]) == ""
    assert bar_chart([("FY2024", None)]) == ""
    print("  OK  no data -> empty string, template's {% if revenue_chart %} hides the section")


def test_single_bar_does_not_divide_by_zero():
    svg = bar_chart([("FY2024", 500.0)])
    assert 'class="bar chart-bar"' in svg
    print("  OK  a single fiscal year still renders (n=1 doesn't blow up the width math)")


def test_exact_and_abbreviated_never_silently_disagree_in_scale():
    """A regression this shape would catch: if fmt_money and the exact
    formatter ever diverged in units (e.g. one in thousands), the two
    numbers shown for the same bar would contradict each other."""
    svg = bar_chart([("FY2024", 1234567000.0)])
    m = re.search(r'data-value="([\d.]+)([BMK]?)".*?data-exact="([\d,]+)"', svg)
    assert m, svg
    label_val, suffix, exact = m.groups()
    mult = {"B": 1e9, "M": 1e6, "K": 1e3, "": 1}[suffix]
    reconstructed = float(label_val) * mult
    exact_num = float(exact.replace(",", ""))
    # The abbreviated label rounds to 2dp, so some drift is expected (e.g.
    # 1.23B vs an exact 1,234,567,000) -- this catches a UNIT mismatch
    # (thousands vs billions), not rounding to 2 decimal places.
    assert abs(reconstructed - exact_num) / exact_num < 0.01, (reconstructed, exact_num)
    print(f"  OK  abbreviated {label_val}{suffix} and exact {exact} agree on scale")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        print(f"{t.__name__}:")
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {e}")
        except Exception as e:                              # noqa: BLE001
            failed += 1
            import traceback
            print(f"  ERROR {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
