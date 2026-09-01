# The front end

React 19 and Vite. The doctor-facing interface for the clinical decision support system:
waiting room and triage, the consultation, reports, the reference library, staff admin.

## Running it

```bash
npm install && npm run dev
```

It needs the Laravel API on port 8000 and the engine on 8001. See the root `README.md` for
starting those.

## Configuration

Copy `.env.example` to `.env`. Both settings have working defaults, so the site runs without
one — they exist so that moving a service is a configuration change, not a code change.

| Setting | Default | Meaning |
|---|---|---|
| `VITE_API_URL` | `/api` | What the browser calls. A relative path keeps the front end and the API on one origin, so there is no CORS to configure and the token is never sent cross-site. Set an absolute URL when they are deployed apart. |
| `VITE_API_PROXY_TARGET` | `http://127.0.0.1:8000` | Where the dev server forwards `/api`. Development only — the built site has no proxy. |

`src/api.js` is the only file that builds a request URL, and `vite.config.js` the only one
that knows about the proxy. Nothing else needs to change to move the backend.

## Deploying it apart from the API

`npm run build` writes `dist/`, which is static files and no proxy. If the API is on another
host, set `VITE_API_URL` to its absolute URL **before building** — Vite bakes the value into
the bundle — and then on the Laravel side add CORS and put the front end's origin in
`config/sanctum.php`.

## A note on this folder

It began as a test harness for the API and grew into the interface. `src/api.js` is the part
worth keeping if it is ever replaced: it is the shape of the contract, including the two
things a client has to get right — the `persisted` flag, and that a 409 is a clinical
refusal written for a doctor to read, not an error to hide.
