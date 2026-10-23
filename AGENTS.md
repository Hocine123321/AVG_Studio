# Running this repository in the Base44 sandbox

The repository root is the **native iOS app** (`AVGStudio/`, `project.yml`,
XcodeGen). It needs Xcode and a simulator, so it cannot run in this Linux
container and cannot be shown in the browser preview. Nothing here changes it.

The runnable application in this repo is its own **Linux desktop/web edition**
in `Pc Version Reference (Linux)/` (FastAPI bridge in `avg/`, React 19 UI in
`web/`). Its README calls that folder read-only reference *for the Swift port*;
it is still the only thing in the repo that boots on Linux, so that is what
`docker-compose.base44.yml` runs - from the source tree, never a built image.

## Layout

| Service | What runs | Notes |
|---|---|---|
| `web` | `npm run dev` (Vite) | host port **3000**, the only public entry point |
| `api` | `python tools/dev_serve_linux.py` (uvicorn) | internal port 8756, reached only through Vite's `/api` and `/ws` proxy |

Single origin on purpose: the bridge's session token travels as an
`X-AVG-Token` header / `?token=` query parameter, so a second origin would only
add a CORS layer. `web/vite.config.ts` proxies both paths.

## Two non-obvious pieces of the wiring

- **The token.** `avg/shell.py` normally opens the window at
  `http://127.0.0.1:<port>/?token=<token>` and `web/src/api.ts` reads it from
  `location.search` once. A preview iframe gets no query string, so
  `web/vite.config.ts` reads `AVG_DEV_TOKEN` and, when it is set, the proxy
  presents that token itself (header for `/api`, query rewrite for the
  `/ws/events` handshake, which browsers cannot give headers). Unset - the
  normal local case - nothing is injected and the client's own token stays the
  only credential.
- **`AVG_DEV_HOST` / `AVG_DEV_PORT` / `AVG_DEV_TOKEN`** in
  `tools/dev_serve_linux.py` are env overrides added so that dev server can
  bind outside loopback. Their defaults are the original `127.0.0.1:8756` /
  `devtoken`. `AVG_DEV_API_URL` in `vite.config.ts` likewise lets the proxy
  target another container (`http://api:8756`); unset, it is
  `127.0.0.1:${AVG_DEV_API_PORT:-8756}` exactly as before.

## Data and credentials

- Library, settings, channels, secrets and caches live under `/data/home`
  (a named volume, `avg-home`), wired through `HOME` / `XDG_*` env vars.
  `docker compose down -v` erases the library; `down` alone keeps it.
- First launch shows the onboarding screen (`onboarded: false` in
  `GET /api/settings`). Accepting the suggestion writes
  `/data/home/Documents/AVG Studio/UserAssets/Config/settings.json`.
- **Provider keys**: `avg/config/secrets.py` reads `VAF_<PROVIDER>_API_KEY`
  from the environment before its encrypted store, so keys can come from
  `/run/base44/app.env` without touching the app. The shipped defaults are
  `groq/llama-3.3-70b-versatile` for text (Groq key) and `elevenlabs` for voice
  (ElevenLabs key); images default to Pollinations, which needs no key.
- **Keyring**: the Settings screen stores a key through `keyring`. A container
  has no D-Bus Secret Service, so the image installs `keyrings.alt` and the
  compose file sets `PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring`.
  That is a sandbox convenience and gives **no real protection** - the Fernet
  key sits in a plaintext file. A real desktop install keeps it in GNOME
  Keyring / KWallet. Prefer the `VAF_*_API_KEY` env vars in this environment.
- AI phases are the only things that need the network. The UI, onboarding,
  projects, Settings and App Health work with no keys at all.

## Verify

```bash
curl -s localhost:3000/ | head -5                      # Vite serves the SPA
curl -s localhost:3000/api/keys                        # proxied + token injected
curl -s localhost:3000/api/health | head -c 400        # components incl. FFmpeg
docker compose -f docker-compose.base44.yml ps         # both services healthy
```

`api`'s healthcheck asserts the bridge answers `GET /api/keys` **and** that
`ffmpeg`/`ffprobe` are on PATH (renders and thumbnails shell out to them). The
`moviepy` row in App Health stays "not installed" by design - it is an optional
fallback the project itself does not declare.

Tests: `docker compose -f docker-compose.base44.yml exec api python -m pytest tests -q`
(run only targeted files while iterating; the sandbox is small).
