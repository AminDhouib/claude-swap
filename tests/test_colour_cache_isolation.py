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
conftest's, and the guard would pass with the thing it guards removed. pytest
runs tests in definition order within a file, so the pair below is a real
ordering: the first latches, the second reads.
"""

from __future__ import annotations

import sys
from io import StringIO

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
