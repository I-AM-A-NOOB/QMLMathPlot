"""Theme engine: Matplotlib style sheets -> Plot style properties (no GUI)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QColor

from qmlmathplot import themes

COLOR_KEYS = ("background", "grid_color", "text_color", "tick_color", "title_color")
FLOAT_KEYS = ("grid_width", "grid_alpha", "line_width", "font_size", "tick_font_size",
              "title_font_size", "tick_length")


def test_bundled_themes_are_listed() -> None:
    names = themes.names()
    assert names[0] == "default"
    for expected in ("ggplot", "dark_background", "classic", "fivethirtyeight",
                     "grayscale", "bmh", "tableau-colorblind10", "seaborn-v0_8-darkgrid"):
        assert expected in names


def test_every_bundled_theme_resolves_to_valid_values() -> None:
    """The whole set must convert without raising, with colours Qt accepts."""
    for name in themes.names():
        resolved = themes.resolve(name)
        assert isinstance(resolved, dict), name
        for key, value in resolved.items():
            assert key in set(themes._SOURCES), f"{name}: unexpected property {key}"
            if key in COLOR_KEYS:
                assert isinstance(value, str), f"{name}.{key}"
                assert QColor(value).isValid(), f"{name}.{key} = {value!r} is not a colour"
            elif key in FLOAT_KEYS:
                assert isinstance(value, float), f"{name}.{key}"
            elif key == "color_cycle":
                assert isinstance(value, list) and value, f"{name}.{key}"
                for entry in value:
                    assert isinstance(entry, str) and QColor(entry).isValid(), f"{name}: {entry!r}"
            elif key in ("grid", "title_bold"):
                assert isinstance(value, bool), f"{name}.{key}"
            elif key == "grid_style":
                assert value in ("-", "--", ":", "-."), f"{name}.{key} = {value!r}"


def test_ggplot_matches_the_style_sheet() -> None:
    resolved = themes.resolve("ggplot")
    assert resolved["background"] == "#E5E5E5"
    assert resolved["grid"] is True
    assert resolved["grid_color"] == "white"
    cycle = resolved["color_cycle"]
    assert isinstance(cycle, list) and cycle[0] == "#E24A33"    # the first cycle colour
    assert resolved["font_size"] == pytest.approx(10 * themes.PT_TO_PX)   # points -> pixels


def test_dark_background_is_dark() -> None:
    resolved = themes.resolve("dark_background")
    assert resolved["background"] == "black"
    assert resolved["text_color"] == "white"


def test_colour_shorthands_are_converted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """matplotlib accepts 'w'/'k', bare hex, float grayscale and C0… cycle references."""
    style = tmp_path / "colours.mplstyle"
    style.write_text(
        "axes.prop_cycle: cycler('color', ['E24A33', '0.5'])\n"
        "axes.facecolor: w\n"
        "grid.color: k\n"
        "text.color: 0.8\n"
        "axes.titlecolor: C1\n"
        "xtick.color: none\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(themes, "THEME_DIR", tmp_path)
    resolved = themes.resolve("colours")
    assert resolved["color_cycle"] == ["#E24A33", "#808080"]
    assert resolved["background"] == "white"
    assert resolved["grid_color"] == "black"
    assert resolved["text_color"] == "#cccccc"
    assert resolved["title_color"] == "#808080"     # C1 -> the second cycle colour
    assert "tick_color" not in resolved             # 'none' has no equivalent here


def test_unknown_keys_and_unknown_theme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    style = tmp_path / "synthetic.mplstyle"
    style.write_text(
        "# comment\n"
        "axes.facecolor: white\n"
        "axes.grid: True\n"
        "spines.left: False\n"          # no spines here
        "figure.figsize: 6.4, 4.8\n"    # no page here
        "legend.fontsize: small\n"      # no legend here
        "axes.titleweight: bold\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(themes, "THEME_DIR", tmp_path)
    resolved = themes.resolve("synthetic")
    assert resolved["background"] == "white"
    assert resolved["grid"] is True
    assert resolved["title_bold"] is True
    assert set(resolved) == {"background", "grid", "title_bold"}

    with pytest.raises(KeyError):
        themes.resolve("no-such-theme")
