"""What the shell must be true of before anything is imported.

The timing itself is measured by hand and recorded in
`.superpowers/sdd/progress.md`; a wall-clock assertion in a suite is a
flake waiting to happen. What is asserted here is the SEAM that makes the
window able to open first.
"""

import sys

from avg.shell import make_token, pick_port, splash_html


def test_port_is_free_and_ephemeral():
    p = pick_port()
    assert 1024 < p < 65536


def test_token_is_unguessable_and_unique():
    a, b = make_token(), make_token()
    assert a != b and len(a) >= 32


def test_the_splash_needs_no_server_no_app_and_no_settings():
    """The property that lets the window open first: this page is built
    from nothing. No port, no token, no `create_app`, no settings file - so
    it can be handed to pywebview before a single heavy import has run."""
    html = splash_html()
    assert "AVG Studio" in html
    # Nothing to fetch: a splash that needed the server running would defeat
    # its own purpose.
    assert "http://" not in html
    assert "https://" not in html


def test_the_shell_does_not_import_the_server_at_module_scope():
    """`fastapi` is 0.42s, `uvicorn` 0.27s and `avg.api.app` 0.72s
    cumulative. Importing any of them at module scope puts all of it back in
    front of the window."""
    import avg.shell as shell

    assert not hasattr(shell, "uvicorn")
    assert not hasattr(shell, "create_app")
    assert not hasattr(shell, "load_settings")
    assert "avg.shell" in sys.modules
