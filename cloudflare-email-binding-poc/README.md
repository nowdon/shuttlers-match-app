# Phase 9 Cloudflare Email binding PoC

This isolated Python Workers PoC imports the production `mail.cloudflare`
adapter and exercises the complete synchronous Flask boundary:

```text
request.environ["workers.env"].EMAIL
-> CloudflareEmailTransport.from_request()
-> pyodide.ffi.run_sync
-> structured payload conversion
-> base64 attachment
-> fake EMAIL.send()
```

`wrangler.jsonc` documents the recommended `EMAIL` `send_email` binding shape,
but the Worker deliberately shadows it with a request-local fake binding. The
fake returns a synthetic `messageId` and validates the received payload without
calling Cloudflare Email Service, so this PoC never sends a real email and does
not require an account, verified sender, destination allowlist, DNS, SPF, DKIM,
or production credentials.

Run the disposable local check with:

```bash
python tests/run_phase9_wrangler_local.py \
  cloudflare-d1-binding-poc/node_modules/.bin/wrangler
```

The check uses Wrangler 4.131.1, local mode only, and disposable state. It does
not use `--remote`, preview bindings, SMTP, a production Worker, or a
production Email Service binding.
