"""Cloudflare-only entrypoint; never import the production app here."""

from workers import wsgi

from poc_app import app

Default = wsgi.entrypoint(app)
