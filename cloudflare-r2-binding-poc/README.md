# Phase 7 local R2 binding PoC

This isolated Python Worker links and imports the production
`R2HistoryArchiveStorage` adapter, then exercises its `put`, `get`, paginated
`list`, and single-key `delete` methods from a synchronous WSGI handler. This
validates the production `pyodide.ffi.run_sync` bridge against the
`HISTORY_ARCHIVES` R2 binding. It uses only Wrangler local storage with synthetic
JSON bytes and has no production bucket ID, route, secret, SMTP configuration, or
remote/preview mode.

Run it through the repository harness:

```bash
python tests/run_phase7_wrangler_local.py \
  cloudflare-d1-binding-poc/node_modules/.bin/wrangler
```
