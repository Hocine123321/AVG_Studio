"""What the shell must be true of before anything is imported.

The timing itself is measured by hand and recorded in
`.superpowers/sdd/progress.md`; a wall-clock assertion in a suite is a
flake waiting to happen. What is asserted here is the SEAM that makes the
window able to open first.
"""

import subprocess
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


def test_startup_check_defaults_to_on_and_can_be_turned_off():
    """A setting the UI saves and nothing reads is the mistake
    `settings_schema.py` records three of. This is the reader."""
    from avg.shell import should_check_at_startup

    assert should_check_at_startup({}) is True
    assert should_check_at_startup({"startup_check": True}) is True
    assert should_check_at_startup({"startup_check": False}) is False
    # A junk value is not a reason to skip a health check.
    assert should_check_at_startup({"startup_check": "yes please"}) is True


def test_reading_the_setting_needs_no_server_and_no_app():
    """Same property the splash has: this runs on the boot path before the
    server exists, so CALLING it must not drag the server/app modules in.

    A prior version of this test only re-asserted
    `should_check_at_startup({"startup_check": False}) is False` - already
    covered three lines above in
    `test_startup_check_defaults_to_on_and_can_be_turned_off`, and no
    evidence at all about import isolation despite the name and docstring
    both claiming it. This subprocess-imports `avg.shell`, calls
    `should_check_at_startup`, and checks that doing so did not pull in
    `fastapi`, `uvicorn`, or `avg.api.app` - the actual "no server, no app"
    property the two prior tests already establish for `avg.shell` itself
    (`test_the_shell_does_not_import_the_server_at_module_scope`) and for
    the splash (`test_the_splash_needs_no_server_no_app_and_no_settings`).
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from avg.shell import should_check_at_startup; "
            "should_check_at_startup({'startup_check': False}); "
            "print('fastapi' in sys.modules, 'uvicorn' in sys.modules, "
            "'avg.api.app' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False False False", (
        "calling should_check_at_startup pulled in the server/app modules "
        f"it is meant to run ahead of: {result.stdout!r}"
    )
