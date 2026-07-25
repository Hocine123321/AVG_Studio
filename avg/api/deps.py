"""Auth for the FastAPI bridge.

Every route in `avg.api.app` requires the session token that is generated
once per process (see `avg.shell.make_token`, Task 8) and handed to
`create_app`. HTTP routes read it from the `X-AVG-Token` header; the
WebSocket handshake is checked separately in `avg.api.app.ws_events`
because browsers cannot set custom headers on a WebSocket handshake - that
one reads a `?token=` query parameter instead.

The same "no custom headers" limitation applies to plain `<img src=...>`
and `<video src=...>` loads (Task 10.5's thumbnail and video routes) - the
browser issues those requests itself, so `api.ts`'s usual
`X-AVG-Token`-header fetch wrapper never gets a chance to run. `require_token`
below accepts either the header OR a `?token=` query parameter for exactly
that reason; every other route keeps working unchanged since `api.ts`
always sends the header.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request, status


async def require_token(
    request: Request,
    x_avg_token: str | None = Header(default=None, alias="X-AVG-Token"),
) -> None:
    """FastAPI dependency guarding every HTTP route in the bridge.

    The expected token lives on `app.state.token`, set once by
    `create_app` - comparing against app state (not a process-wide global)
    keeps each app instance independent, which is what lets tests build a
    fresh, isolated app per test.
    """
    provided = x_avg_token or request.query_params.get("token")
    if provided != request.app.state.token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid X-AVG-Token",
        )
