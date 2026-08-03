"""Pytest fixtures for Claude Switch tests."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_swap import macos_keychain as _macos_keychain


class _KeychainStore:
    """In-memory ``(service, account) -> secret`` map standing in for the real
    macOS Keychain so unit tests never shell out to ``security`` or ``keyring``."""

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    # Mirrors the ``macos_keychain`` (security CLI) contract.
    def get_password(self, service: str, account: str) -> str | None:
        return self.data.get((service, account))

    def item_exists(self, service: str, account: str) -> bool:
        return (service, account) in self.data

    def set_password(self, service: str, account: str, password: str) -> None:
        self.data[(service, account)] = password

    def delete_password(self, service: str, account: str) -> None:
        self.data.pop((service, account), None)  # absent = no-op (rc 44)


def _make_fake_keyring() -> types.ModuleType:
    """Build an in-memory stand-in for the ``keyring`` module (which would hit the
    real Keychain on macOS) for code paths that lazily ``import keyring``."""

    class _Errors:
        class PasswordDeleteError(Exception):
            pass

        class PasswordSetError(Exception):
            pass

        class KeyringError(Exception):
            pass

    store: dict[tuple[str, str], str] = {}
    mod = types.ModuleType("keyring")
    mod.errors = _Errors  # type: ignore[attr-defined]

    def get_password(service: str, username: str):
        return store.get((service, username))

    def set_password(service: str, username: str, password: str) -> None:
        store[(service, username)] = password

    def delete_password(service: str, username: str) -> None:
        if (service, username) not in store:
            raise _Errors.PasswordDeleteError("not found")
        del store[(service, username)]

    mod.get_password = get_password  # type: ignore[attr-defined]
    mod.set_password = set_password  # type: ignore[attr-defined]
    mod.delete_password = delete_password  # type: ignore[attr-defined]
    return mod


@pytest.fixture(autouse=True)
def _isolate_real_home(request, tmp_path_factory, monkeypatch):
    """Safety net: no test may read or write the developer's real ``$HOME``.

    Some tests (CLI/TUI argument tests that call ``main()``, etc.) construct a real
    ``ClaudeAccountSwitcher`` without the ``temp_home`` fixture. Without isolation
    that switcher resolves to the real ``~/.claude-swap-backup`` — writing logs,
    running data migrations, and reading the real account list. Redirect ``$HOME``
    to a throwaway dir unless the test already uses ``temp_home`` (which sets its
    own). Runs first (autouse, before the keychain guard and other fixtures).

    Exempt the ``tmp_keychain`` fixture too: the macOS-CI integration tests that
    use it drive the real ``security`` CLI (``default-keychain`` /
    ``list-keychains``), which needs the real ``$HOME`` to locate
    ``~/Library/Keychains``. An isolated ``$HOME`` makes those commands fail. The
    fixture itself swaps the default keychain to a throwaway one and restores it.

    Always neutralize ``CLAUDE_CONFIG_DIR`` and ``XDG_DATA_HOME`` (even for
    ``temp_home`` tests): both bypass ``$HOME`` in path resolution
    (``paths.get_global_config_path``/``get_backup_root``), so a developer with
    either exported could otherwise have tests read/write real Claude config or
    backup paths — and on macOS that leads back to the real Keychain. Tests that
    exercise those vars set them explicitly, overriding this.
    """
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_SECURESTORAGE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    if "temp_home" in request.fixturenames:
        return  # temp_home provides its own isolated home
    if "tmp_keychain" in request.fixturenames:
        return  # real-keychain integration tests need the real $HOME
    safe_home = tmp_path_factory.mktemp("isolated_home")
    (safe_home / ".claude").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(safe_home))
    monkeypatch.setenv("USERPROFILE", str(safe_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: safe_home)


@pytest.fixture(autouse=True)
def block_real_keychain(request, monkeypatch):
    """Safety net: no test may touch the real macOS Keychain.

    Replaces the ``security``-CLI wrapper (``claude_swap.macos_keychain``) with an
    in-memory fake and injects a fake ``keyring`` module (for the lazy
    ``import keyring`` paths in purge/migrations). Tests marked
    ``@pytest.mark.no_keychain_fake`` opt out — either because they mock
    ``subprocess`` themselves (the wrapper's own unit tests) or because they run
    against a temporary keychain on GitHub Actions.

    Yields the in-memory :class:`_KeychainStore` so tests can seed/inspect it.
    """
    if request.node.get_closest_marker("no_keychain_fake"):
        yield None
        return
    store = _KeychainStore()
    monkeypatch.setattr(_macos_keychain, "get_password", store.get_password)
    monkeypatch.setattr(_macos_keychain, "item_exists", store.item_exists)
    monkeypatch.setattr(_macos_keychain, "set_password", store.set_password)
    monkeypatch.setattr(_macos_keychain, "delete_password", store.delete_password)
    monkeypatch.setitem(sys.modules, "keyring", _make_fake_keyring())
    yield store


@pytest.fixture
def temp_home(tmp_path: Path):
    """Create a temporary home directory for testing."""
    home = tmp_path / "home"
    home.mkdir()

    # Create .claude directory structure
    claude_dir = home / ".claude"
    claude_dir.mkdir()

    # Patch HOME environment variable (and USERPROFILE for Windows)
    env_patch = {"HOME": str(home), "USERPROFILE": str(home)}
    with patch.dict(os.environ, env_patch):
        # Also patch Path.home() directly for cross-platform compatibility
        with patch("pathlib.Path.home", return_value=home):
            yield home


@pytest.fixture
def mock_claude_config(temp_home: Path):
    """Create a mock Claude configuration file."""
    config = {
        "oauthAccount": {
            "emailAddress": "test@example.com",
            "accountUuid": "test-uuid-1234",
        }
    }
    config_path = temp_home / ".claude.json"
    config_path.write_text(json.dumps(config))
    return config_path


@pytest.fixture
def mock_credentials_file(temp_home: Path):
    """Create a mock credentials file for Linux/WSL."""
    creds = {"accessToken": "test-token", "refreshToken": "test-refresh"}
    cred_path = temp_home / ".claude" / ".credentials.json"
    cred_path.write_text(json.dumps(creds))
    return cred_path


@pytest.fixture
def sample_sequence_data():
    """Sample sequence.json data."""
    return {
        "activeAccountNumber": 1,
        "lastUpdated": "2024-01-01T00:00:00Z",
        "sequence": [1, 2],
        "accounts": {
            "1": {
                "email": "account1@example.com",
                "uuid": "uuid-1",
                "added": "2024-01-01T00:00:00Z",
            },
            "2": {
                "email": "account2@example.com",
                "uuid": "uuid-2",
                "added": "2024-01-02T00:00:00Z",
            },
        },
    }


@pytest.fixture
def mock_org_claude_config(temp_home: Path):
    """Claude config file with an active organization account."""
    config = {
        "oauthAccount": {
            "emailAddress": "user@example.com",
            "accountUuid": "user-uuid-1234",
            "organizationUuid": "org-uuid-5678",
            "organizationName": "Acme Corp",
            "organizationRole": "primary_owner",
            "displayName": "Test User",
        }
    }
    config_path = temp_home / ".claude.json"
    config_path.write_text(json.dumps(config))
    return config_path


@pytest.fixture
def mock_personal_claude_config(temp_home: Path):
    """Claude config file with a personal account (no organizationUuid)."""
    config = {
        "oauthAccount": {
            "emailAddress": "user@example.com",
            "accountUuid": "user-uuid-1234",
        }
    }
    config_path = temp_home / ".claude.json"
    config_path.write_text(json.dumps(config))
    return config_path


@pytest.fixture
def sample_sequence_data_pre_v06():
    """Pre-v0.6.0 sequence.json data without organizationUuid/Name fields."""
    return {
        "activeAccountNumber": 1,
        "lastUpdated": "2024-01-01T00:00:00Z",
        "sequence": [1, 2],
        "accounts": {
            "1": {
                "email": "user@example.com",
                "uuid": "user-uuid-1234",
                "added": "2024-01-01T00:00:00Z",
            },
            "2": {
                "email": "other@example.com",
                "uuid": "other-uuid-5678",
                "added": "2024-01-02T00:00:00Z",
            },
        },
    }


@pytest.fixture
def sample_sequence_data_with_org():
    """sequence.json data with mixed organization and personal accounts."""
    return {
        "activeAccountNumber": 1,
        "lastUpdated": "2024-01-01T00:00:00Z",
        "sequence": [1, 2],
        "accounts": {
            "1": {
                "email": "user@example.com",
                "uuid": "user-uuid",
                "organizationUuid": "org-uuid-5678",
                "organizationName": "Acme Corp",
                "added": "2024-01-01T00:00:00Z",
            },
            "2": {
                "email": "user@example.com",
                "uuid": "user-uuid",
                "organizationUuid": "",
                "organizationName": "",
                "added": "2024-01-02T00:00:00Z",
            },
        },
    }


@pytest.fixture(autouse=True)
def _deterministic_poll_jitter(monkeypatch):
    """Zero the poll-plan jitter so cadence tests are clock-exact; the jitter
    itself is exercised in test_poll_policy via an injected rng."""
    monkeypatch.setattr("claude_swap.poll_policy.JITTER_FRAC", 0.0)


@pytest.fixture(autouse=True)
def _deterministic_colour(monkeypatch):
    """A developer's terminal must not decide whether the suite passes.

    ``printer._detect_color_support`` honours ``FORCE_COLOR``/``NO_COLOR``
    before it consults ``isatty()`` — correct for the CLI, where those
    variables exist to override detection, and wrong under pytest, where
    stdout is captured and every assertion on plain output then breaks.
    Measured with ``FORCE_COLOR=3`` exported: 11 failures in test_switcher.py
    reading ``assert 'Skipping Account-2 (disabled)' in
    '\\x1b[38;5;173mSkipping\\x1b[0m Account-2 (disabled)'``, and the same
    tree green with the variable unset.

    Scrubbing the variables is not enough on its own, because detection
    CACHES. ``colors_enabled()`` latches the first answer into
    ``printer._colors_enabled`` and every later call returns it, so one test
    that latches ``True`` decides the styling for every test after it —
    whatever the environment says by then. Under ``pytest -s`` stdout stays
    the real terminal, ``isatty()`` is True, and the tests that latch are
    ordinary ones nobody would suspect: ``test_migrations``, ``test_printer``,
    ``test_swap_accounts``, ``test_transfer``, ``test_tui``. Measured on a pty
    with no colour variable set at all::

        pytest tests/test_migrations.py tests/test_switcher.py -q -s
        11 failed, 382 passed

    which is the same eleven assertions, in the same file, that motivated this
    fixture. So the cache is reset too.

    This line was briefly removed on the grounds that the cache is ``None`` on
    entry for every test, i.e. that resetting it proves nothing. THE
    OBSERVATION IS RIGHT AND THE INFERENCE IS BACKWARDS: the ``None`` is
    produced BY this line. ``monkeypatch.setattr`` restores the pre-test value
    at teardown, so every test both enters and leaves with the cache unset —
    which is the whole point, and reads as evidence of inertness only if you
    assume the state would have been ``None`` anyway.

    THE SHAPE, MEASURED WITH A PROBE AS THE FIRST STATEMENT OF THIS FIXTURE
    BODY — and stated without digits, on purpose::

        as shipped                                 every test enters None
        same reset, plain assignment (no restore)   many enter dirty
        reset removed entirely                      most enter dirty

    The middle row is the control that separates the two explanations:
    identical reset, only the restore dropped, and the cache is still dirty on
    entry. So the RESTORE is the operative mechanism, not the probe's position.

    The value matters because a wrong one satisfies the shape: pinning `False`
    instead of `None` leaves the probe reading all-None, identical to shipped,
    while the reset has stopped un-latching and started PINNING — a different
    mechanism with the same signature. Both directions are covered:
    `test_printer.py`'s `TestColourEnvDoesNotLeakIntoTests` dies under either
    pin.

    THE COUNTS THAT USED TO BE HERE ARE GONE, and this is the sixth stale-figure
    correction, not the first. The five before it each went stale for the SAME
    reason: the commit that changes the guard file also changes the denominator.
    The fifth was this branch's own deletion of `test_printer.py`'s module-local
    `_reset_color_cache`, which moved rows 2 and 3 and made a sentence three
    lines above this one false, in a paragraph written to warn about exactly
    that. Row 3 also poisons its own measurement: the guard file latches the
    cache on purpose and, with the reset gone, everything after it inherits the
    latch — so the count is partly of this file's own doing.

    A number nobody can re-take is not evidence, and hand-maintaining thirty of
    them across two files is what produced five wrong ones. What survives is
    what still measures: `shipped == all None` (the VALUE, not merely "not
    latched") and `removed == mostly not-None`. Both mutations are run by the
    suite, so the claim is checked rather than recorded.

    Tests that exercise the detection itself set the variables explicitly,
    which overrides this.
    """
    # Ordering-PROOF half of the guard. `test_colour_cache_isolation.py`'s
    # latch/read pairs read as the guard, but they only fire in one ordering:
    # with the reset mutated out AND these two assertions disabled — the only
    # state in which the pairs are what is being tested — the file-scoped run
    # escapes at 9 of seeds 1-30 for the cache and 17 of 30 for the theme.
    # These assertions run before EVERY test, so no test ORDERING hides a
    # latch. One shape does escape them, and it is worth naming rather than
    # overclaiming: a fixture that latches during SETUP after this one. A
    # conftest fixture runs before a module-level autouse fixture of the same
    # scope, so an adversary appended to `test_printer.py` latches after the
    # snapshot is taken — measured, 8 failures all inside that file and this
    # assertion silent. That is exactly the shape of the two module-local
    # fixtures this branch deletes, so the fix is to not have them, which is
    # what it does; the snapshot bounds ordering, not setup order.
    #
    # A superseded version of this comment claimed the FULL suite was green at
    # seeds 4 and 5 under the same mutant. Re-measured: 20 failed and 18 failed.
    # It went stale from this branch's own deletion of `test_printer.py`'s
    # module-local reset — 9 of the seed-4 failures are in that file — which is
    # the fifth time a figure here rotted for that reason and the reason the
    # counts above this line are gone.
    #
    # The 9 escaping seeds are 4 5 7 12 13 24 25 27 29 — the same list
    # `test_colour_cache_isolation.py` deleted as "no longer reconstructible".
    # It is reconstructible; that file sampled only escapes and read them as
    # noise. Kept here as a COUNT, which is what the claim needs, rather than
    # as a table of digits.
    from claude_swap import appearance as _appearance
    from claude_swap import printer as _printer

    # SNAPSHOT FIRST, ASSERT ON THE SNAPSHOT. The obvious form — asserting the
    # globals directly — depends on sitting ABOVE the resets, and nothing in
    # the code says so: moving the two `setattr` lines up makes each assert
    # read the value the reset just wrote, which is green on seq, `-n 4` and
    # nine seeds with no line deleted. Proof that is a real kill and not a
    # no-op: a session-scoped fixture latching `True`/`"light"` gives 1702
    # errors in the shipped order and 1702 PASSED reordered — same latch,
    # opposite verdict.
    #
    # Reading into a local on the fixture's first statement makes the
    # detection independent of where the assertions live.
    inherited = (_printer._colors_enabled, _printer._theme)
    assert inherited[0] is None, (
        "a previous test latched the colour cache and nothing reset it, or a "
        "broader-scoped fixture latched it during setup"
    )
    assert inherited[1] == "dark", (
        "a previous test latched the theme, or a broader-scoped fixture "
        "latched it during setup"
    )
    # Both scrubs are killed individually ON A CLEAN ENVIRONMENT, which is the
    # only coverage worth having: measured, with neither variable exported and
    # both lines deleted, the whole suite was green — so before
    # `test_printer.py`'s class-scoped `_exported` fixture these two lines were
    # dead code on every CI runner, guarded only on a box where the bug was
    # already happening. Drop either one now and its own test dies.
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("claude_swap.printer._colors_enabled", None)
    # The OTHER latched global in the same module, closed on the same grounds.
    # `tui/app.py` calls `printer.set_theme("light")`, a plain assignment with
    # nothing restoring it, so with this line removed the tests after it enter
    # with the light palette — spread across several files, not just the one
    # that latched, so the latch outlives its own reader. Green today only
    # because none of them asserts a palette code, which is exactly what was
    # true of `_colors_enabled` until a developer exported FORCE_COLOR.
    #
    # (A count and a per-file breakdown lived here and in
    # `test_colour_cache_isolation.py`, hand-copied into both and wrong in both
    # by the time it was checked — it missed `test_printer.py`, the file this
    # branch edits. The leak is what matters and the mutation below proves it;
    # the digits only had to be maintained in two places.)
    monkeypatch.setattr("claude_swap.printer._theme", "dark")
    # The third `global`-rebound name in the package. Inert under the TERM pin
    # below, which makes `detect_terminal_background` return before ever
    # writing it — but the pin is a guarantee about the tty QUERY, not about
    # the cache, and the cache then latches that `None` against any test that
    # stubs `_query_terminal_background`. Measured, two added tests and no
    # mutation of this fixture: a leaker at the top of collection poisons 28
    # later tests sequentially and 473 at `--randomly-seed=7`, and a
    # detect-then-read pair fails while the reader alone passes — the same
    # shape this file's guard narrates for `_colors_enabled`.
    #
    # `tests/test_appearance.py` carries a module-local reset for exactly this
    # reason, which is the layer this fixture exists to replace.
    monkeypatch.setattr("claude_swap.appearance._cache", _appearance._UNSET)
    # And the OTHER thing a terminal decides: `appearance.detect_terminal_background`
    # puts the tty into cbreak, writes an OSC-11 query, and BLOCKS reading stdin
    # for up to a second. Under `pytest -s` stdin is the developer's real
    # terminal, so the suite emits escape bytes at it and can swallow a
    # keypress. Measured on a pty: reached 8 times in one run, on fd 0.
    #
    # `TERM=dumb` is the function's own documented short-circuit — it returns
    # None before touching termios — so this closes the path rather than
    # patching around it. It is a guarantee about the QUERY and not about the
    # cache, which is why the cache is reset above: the pin makes detection
    # return None fast, and the cache then latches that None against any test
    # that stubs `_query_terminal_background`.
    #
    # No test outside test_appearance.py asserts on a palette code, so this was
    # not flipping results; it is the same class this fixture exists for, one
    # module over.
    #
    # Measured under a pty with `-s`, instrumenting `tty.setcbreak` itself and
    # with TMUX/STY unset: 0 termios entries with this pin, dozens without it,
    # across test_cli.py, test_tui.py and this file's own guard. The wall clock
    # says the same thing without any instrumentation — the run goes from ~41s
    # to ~72s, which is the OSC-11 read timing out over and over.
    #
    # Stated as a magnitude rather than a count on purpose: two earlier notes
    # here gave 8-and-2 and then 2-and-0, and the second was off by more than
    # an order of magnitude. The count depends on how many tests reach
    # detection, which every commit moves; "zero versus many, and the suite
    # takes half again as long" is the part that stays true and that anyone can
    # re-take.
    #
    # Pinned by test_the_suite_does_not_query_the_developers_terminal, which
    # opens a real pty so both isatty() calls are genuinely True and the TERM
    # check at appearance.py:97 is the only gate left, then reads the master to
    # see whether the query went out. Measured: passes with this line, fails
    # without it under TERM unset, xterm-256color and screen-256color.
    #
    # That test also unsets TMUX and STY, and deliberately so: appearance.py:99
    # short-circuits on either, so a developer running inside tmux would get a
    # green from the WRONG gate and the TERM pin would look load-bearing when
    # it was not. This fixture does not pin TMUX/STY — nothing needs it while
    # TERM is pinned first — but the guard test has to strip them or it proves
    # nothing about the line it guards.
    #
    # An earlier version of this comment said no test COULD pin it, on the
    # grounds that "the detection short-circuits on the tty check BEFORE it ever
    # consults TERM". That inverts the source — appearance.py reads TERM at 97
    # and isatty() at 107. Both gates block under plain pytest, so the
    # conclusion was accidentally safe while the reason was false, and the false
    # reason is what made an honest guard look impossible.
    monkeypatch.setenv("TERM", "dumb")
