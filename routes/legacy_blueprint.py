import functools
import sys

from flask import Blueprint
from flask.blueprints import BlueprintSetupState


class LegacyEndpointBlueprintSetupState(BlueprintSetupState):
    """Register Blueprint routes without changing their historical endpoints."""

    def add_url_rule(self, rule, endpoint=None, view_func=None, **options):
        if self.url_prefix is not None:
            if rule:
                rule = "/".join((self.url_prefix.rstrip("/"), rule.lstrip("/")))
            else:
                rule = self.url_prefix

        options.setdefault("subdomain", self.subdomain)
        if endpoint is None:
            endpoint = view_func.__name__

        defaults = self.url_defaults
        if "defaults" in options:
            defaults = dict(defaults, **options.pop("defaults"))

        dependency_module = sys.modules.get(self.blueprint.dependency_module)
        route_globals = view_func.__globals__

        @functools.wraps(view_func)
        def synchronized_view(*args, **kwargs):
            if dependency_module is not None:
                route_globals.update(
                    (name, value)
                    for name, value in dependency_module.__dict__.items()
                    if not name.startswith("__") and not name.endswith("_bp")
                )
            return view_func(*args, **kwargs)

        self.app.add_url_rule(
            rule,
            endpoint,
            synchronized_view,
            defaults=defaults,
            **options,
        )


class LegacyEndpointBlueprint(Blueprint):
    """A Blueprint that preserves pre-Blueprint endpoint names."""

    def __init__(self, *args, dependency_module, **kwargs):
        super().__init__(*args, **kwargs)
        self.dependency_module = dependency_module

    def make_setup_state(self, app, options, first_registration=False):
        return LegacyEndpointBlueprintSetupState(
            self,
            app,
            options,
            first_registration,
        )
