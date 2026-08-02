"""One test must not decide the styling of the next one.

Scrubbing ``FORCE_COLOR``/``NO_COLOR`` is only half the job, because detection
CACHES. ``colors_enabled()`` latches its first answer into
``printer._colors_enabled`` and every later call returns it, so a single
earlier test that latched ``True`` styles every assertion after it no matter
how thoroughly the environment is cleaned.

Measured, with no colour variable set at all — under ``pytest -s`` stdout stays
the real terminal, so ``isatty()`` is True and the latch happens in ordinary
tests (``test_migrations``, ``test_printer``, ``test_swap_accounts``,
``test_transfer``, ``test_tui``)::

    pytest tests/test_migrations.py tests/test_switcher.py -q -s
    11 failed, 382 passed

the same eleven assertions in the same file that motivated the scrub.

This lives in its own file deliberately. ``test_printer.py`` has a module-local
``_reset_color_cache`` fixture that clears the cache around every one of its
tests, so a leak staged there is cleaned up by that fixture rather than by
conftest's, and the guard would pass with the thing it guards removed.
Falsified rather than assumed: staging the identical pair at the end of
``test_printer.py`` and neutering conftest's reset leaves that file and the
whole suite green — the module-local fixture swallows the leak.

pytest runs tests in definition order within a file, so the pair below is a
real ordering: the first latches, the second reads.

THE PAIR IS THE GUARD; EITHER ALONE PROVES NOTHING, AND THE ORDERING IT NEEDS
IS NOT GUARANTEED. Measured with the reset neutered::

    pytest tests/test_colour_cache_isolation.py            1 failed, 1 passed
    pytest ...::test_the_next_test_is_not_styled_by_it      1 passed

Selecting only the reader skips the latch, so it is green over a broken
fixture — and so does ANY ordering that puts the reader first. Measured with
the guard mutated by line number (landing asserted), seeds named so the figure
is re-takeable::

    file-scoped, seeds 1-30   cache escaped  6 (4 5 12 13 24 25)
                              theme escaped 17 (1 2 7 8 9 11 12 14 16 17 18
                                                19 20 21 23 26 28)
    full suite, -p randomly   cache: seeds 4 and 5 -> 1702 passed, 3 skipped
                              theme: seeds 1 and 2 -> 1702 passed, 3 skipped
    file-scoped, -n 4         both mutants -> 5 passed

So the escape is NOT bounded to file-scoped runs, which an earlier version of
this paragraph claimed. Under `-n 4` the two land on different workers, hence
different processes, and neither latch reaches its reader at all.

What closes the hole is the post-condition in `conftest._deterministic_colour`,
which asserts both globals are unlatched on entry to EVERY test — no ordering
required. Same mutants, same seeds: 1694 and 1684 errors. This pair stays as
the readable narrative of what the leak looks like; it is not what makes the
suite safe. Merging the two into one test would not help either — it would
have to reset the global itself, which is the thing under test.
"""

from __future__ import annotations

import os
import sys
from io import StringIO

import pytest

from claude_swap import printer


def test_a_test_may_latch_the_colour_cache():
    """Stands in for the ordinary tests that latch it in a real run."""
    printer._colors_enabled = True
    assert printer.colors_enabled() is True


def test_the_next_test_is_not_styled_by_it(monkeypatch):
    """The reset in ``_deterministic_colour`` is what makes this pass.

    Without it this test inherits the ``True`` latched above and
    ``accent()`` returns the escape-wrapped string — the exact failure the
    eleven ``test_switcher`` assertions report.

    stdout is pinned to a StringIO as the rest of the suite does, so that with
    the cache cleared detection falls through to ``isatty()`` and answers
    False. Asserting on OUTPUT rather than on ``printer._colors_enabled``:
    checking the private flag would pass for free on any run where nothing had
    latched it yet, which is most of them.
    """
    monkeypatch.setattr(sys, "stdout", StringIO())
    assert printer.accent("Skipping") == "Skipping"
    assert "\x1b[" not in printer.muted("usage")



def test_a_test_may_latch_the_theme(monkeypatch):
    """Stands in for `tui/app.py`'s `set_theme("light")`, which nothing restores."""
    monkeypatch.setenv("FORCE_COLOR", "1")
    printer._colors_enabled = None
    printer.set_theme("light")
    assert "38;2;149;76;42" in printer.accent("x")   # premise: light is live


def test_the_next_test_is_not_themed_by_it(monkeypatch):
    """The `_theme` reset is what makes this pass.

    `printer._theme` is the other latched global in this module, and the leak
    is real: measured 1582 dark / 118 light on fixture entry before the reset,
    the 118 being test_usage_store (90), test_update_check (26) and test_tui
    (2). Green without it only because none of those asserts a palette code —
    which is exactly what was true of `_colors_enabled` until someone exported
    FORCE_COLOR.

    Asserts on the palette bytes rather than on `printer._theme`, for the same
    reason the cache guard above asserts on output: the private name being
    right proves nothing about what a caller renders.
    """
    monkeypatch.setenv("FORCE_COLOR", "1")
    printer._colors_enabled = None
    assert "38;5;173" in printer.accent("x"), (
        "the previous test's light theme outlived it"
    )


@pytest.mark.skipif(
    os.name == "nt", reason="pty/termios are POSIX; the query returns at line 95 there"
)
def test_the_suite_does_not_query_the_developers_terminal(monkeypatch):
    """`detect_terminal_background` must not reach a real tty from the suite.

    POSIX only, and not merely because `pty` is missing on Windows: the
    function's first gate is `os.name == "nt"`, so there is no terminal query
    to guard there in the first place.

    It puts the tty into cbreak, writes an OSC-11 query, and blocks reading
    stdin for up to a second. Under `pytest -s` stdin IS the developer's
    terminal, so the suite emits escape bytes at it and can swallow a keypress.

    A real pty makes both `isatty()` calls genuinely True, which removes the
    gate that masks this under plain pytest — so the only thing left standing
    between the suite and the terminal is the fixture's TERM pin, and the
    assertion reads the pty master to see whether the query actually went out.
    Asserting on the BYTES rather than on `os.environ["TERM"]`: the variable
    being right proves nothing about whether the function short-circuited, and
    a box whose TERM is already dumb would pass for free.

    Deliberately does NOT set TERM itself. Doing so overrides the fixture pin
    and turns this into a test of its own setup — measured, it flips a passing
    guard into a failing one.
    """
    import io
    import pty

    from claude_swap import appearance

    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("STY", raising=False)
    monkeypatch.setattr(appearance, "_cache", appearance._UNSET)

    master, slave = pty.openpty()
    try:
        stream = io.TextIOWrapper(
            io.FileIO(slave, "r+", closefd=False), write_through=True
        )
        monkeypatch.setattr(sys, "stdin", stream)
        monkeypatch.setattr(sys, "stdout", stream)
        assert sys.stdin.isatty() and sys.stdout.isatty(), "premise: a real tty"

        assert appearance.detect_terminal_background() is None

        os.set_blocking(master, False)
        try:
            emitted = os.read(master, 4096)
        except BlockingIOError:
            emitted = b""
    finally:
        os.close(master)
        os.close(slave)

    assert b"\x1b]11;?" not in emitted, (
        f"the OSC-11 query hit the terminal: {emitted!r}"
    )
