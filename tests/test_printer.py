"""Tests for the printer module."""

from __future__ import annotations

import sys
from io import StringIO

import pytest

from claude_swap import printer


@pytest.fixture(autouse=True)
def _reset_color_cache():
    """Reset the color detection cache before each test."""
    printer._colors_enabled = None
    printer._theme = "dark"
    yield
    printer._colors_enabled = None
    printer._theme = "dark"


class TestColorDetection:
    """Tests for color support detection."""

    def test_no_color_env_disables(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert printer._detect_color_support() is False

    def test_no_color_empty_value_disables(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "")
        assert printer._detect_color_support() is False

    def test_force_color_enables(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        assert printer._detect_color_support() is True

    def test_non_tty_disables(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        monkeypatch.setattr(sys, "stdout", StringIO())
        assert printer._detect_color_support() is False

    def test_dumb_term_disables(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        monkeypatch.setenv("TERM", "dumb")
        # Need a fake tty
        fake_stdout = StringIO()
        fake_stdout.isatty = lambda: True  # type: ignore[attr-defined]
        monkeypatch.setattr(sys, "stdout", fake_stdout)
        if sys.platform != "win32":
            assert printer._detect_color_support() is False

    def test_colors_enabled_caches(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        assert printer.colors_enabled() is True
        # Even after removing FORCE_COLOR, cached value persists
        monkeypatch.delenv("FORCE_COLOR")
        assert printer.colors_enabled() is True


class TestStyling:
    """Tests for styling functions."""

    def test_style_with_colors_disabled(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert printer.accent("hello") == "hello"
        assert printer.muted("hello") == "hello"
        assert printer.dimmed("hello") == "hello"
        assert printer.bolded("hello") == "hello"
        assert printer.bold_accent("hello") == "hello"

    def test_style_with_colors_enabled(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        result = printer.accent("hello")
        assert "hello" in result
        assert "\033[38;5;173m" in result
        assert "\033[0m" in result

    def test_muted_with_colors_enabled(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        result = printer.muted("org name")
        assert "\033[38;5;250m" in result
        assert "org name" in result

    def test_dimmed_with_colors_enabled(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        result = printer.dimmed("secondary")
        assert "\033[2m" in result
        assert "secondary" in result

    def test_bolded_with_colors_enabled(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        result = printer.bolded("header")
        assert "\033[1m" in result
        assert "header" in result

    def test_bold_accent_with_colors_enabled(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        result = printer.bold_accent("(active)")
        assert "\033[1m" in result
        assert "\033[38;5;173m" in result
        assert "(active)" in result


class TestThemePalette:
    """Tests for set_theme and the per-theme color palette."""

    def test_light_theme_changes_accent(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        printer._colors_enabled = None
        printer.set_theme("light")
        assert "38;2;149;76;42" in printer.accent("x")   # #954c2a
        printer.set_theme("dark")
        assert "38;5;173" in printer.accent("x")

    def test_light_theme_error_uses_light_red(self, monkeypatch, capsys):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        printer._colors_enabled = None
        printer.set_theme("light")
        printer.error("boom")
        assert "38;2;173;49;40" in capsys.readouterr().err   # #ad3128

    def test_unknown_theme_falls_back_to_dark(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        printer._colors_enabled = None
        printer.set_theme("bogus")
        assert "38;5;173" in printer.accent("x")


class TestLinePrinters:
    """Tests for line-level print functions."""

    def test_error_prints_to_stderr(self, monkeypatch, capsys):
        monkeypatch.setenv("NO_COLOR", "1")
        printer.error("something failed")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "something failed" in captured.err

    def test_error_with_color(self, monkeypatch, capsys):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        printer.error("something failed")
        captured = capsys.readouterr()
        assert "\033[31m" in captured.err

    def test_warning_prints_to_stdout(self, monkeypatch, capsys):
        monkeypatch.setenv("NO_COLOR", "1")
        printer.warning("be careful")
        captured = capsys.readouterr()
        assert "be careful" in captured.out

    def test_warning_with_color(self, monkeypatch, capsys):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        printer.warning("be careful")
        captured = capsys.readouterr()
        assert "\033[33m" in captured.out


def test_force_color_overrides_and_restores():
    from claude_swap import printer
    saved = printer._colors_enabled
    try:
        printer._colors_enabled = False
        with printer.force_color():
            assert printer.colors_enabled() is True
            assert printer.accent("X") == "\x1b[38;5;173mX\x1b[0m"
        assert printer._colors_enabled is False
    finally:
        printer._colors_enabled = saved


class TestForceUtf8Output:
    """Tests for force_utf8_output (issue #113: cp1252 console crash)."""

    def test_reconfigures_legacy_stream_to_utf8(self, monkeypatch):
        # A cp1252-encoded stdout raises on the tool's glyphs before the fix.
        import io

        stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        with pytest.raises(UnicodeEncodeError):
            stream.write("● → ├ ─ └")
            stream.flush()

        monkeypatch.setattr(sys, "stdout", stream)
        monkeypatch.setattr(sys, "stderr", stream)
        printer.force_utf8_output()

        assert stream.encoding == "utf-8"
        # No longer raises now that the stream encodes UTF-8.
        stream.write("● → ├ ─ └")
        stream.flush()

    def test_no_op_on_streams_without_reconfigure(self, monkeypatch):
        # StringIO has no reconfigure(); the guard must skip it silently.
        monkeypatch.setattr(sys, "stdout", StringIO())
        monkeypatch.setattr(sys, "stderr", StringIO())
        printer.force_utf8_output()  # must not raise


class TestColourEnvDoesNotLeakIntoTests:
    """A developer's terminal must not decide whether the suite passes.

    See ``_deterministic_colour`` in conftest for the measurement. These
    assert on OUTPUT: checking only ``"FORCE_COLOR" not in os.environ``
    passes for free on any box that never exported it, which is most
    boxes and every CI runner.
    """

    def test_styled_output_is_plain(self, monkeypatch):
        """The reduced form of the 11 test_switcher failures.

        Guards the FORCE_COLOR scrub: without it this returns the styled
        string on any machine that exported the variable.

        stdout is pinned to a StringIO, as the rest of this file does. The
        fixture guarantees the variables are gone, not that stdout is not a
        tty — so under ``pytest -s`` on a terminal, detection correctly falls
        through to isatty() and returns True. Asserting unconditionally made
        the outcome depend on how the developer invoked pytest, which is the
        failure class this whole change exists to remove.
        """
        monkeypatch.setattr(sys, "stdout", StringIO())
        assert printer.accent("Skipping") == "Skipping"
        assert "\x1b[" not in printer.muted("usage")

    def test_detection_reaches_isatty_rather_than_an_override(self, monkeypatch):
        """Guards the NO_COLOR scrub, which the test above cannot see.

        Both scrubs land on the same OUTPUT — plain — so a fixture that
        cleared only FORCE_COLOR would still pass the test above while
        leaving NO_COLOR free to steer any suite asserting that styling IS
        present. What separates them is WHY the answer is plain: with the
        variables gone, detection has to fall through to the captured-stdout
        ``isatty()`` check, so forcing that to report a TTY must flip it.
        A surviving NO_COLOR would pin it False regardless.
        """
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        assert printer.colors_enabled() is True
