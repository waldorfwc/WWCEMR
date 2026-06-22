"""Importing `app.models` registers EVERY ORM model's table in
`Base.metadata`.

Each submodule here is auto-imported below, so table registration can never be
silently lost by a refactor that stops importing a model elsewhere (e.g. a
router no longer importing its model). `init_db()` imports this package before
`Base.metadata.create_all`, guaranteeing every table is created.

Safe because no model module imports `app.routers`/`app.services` (no import
cycle) and none does a package-level `from app.models import X` intra-import.
Non-table modules (`guid`, `mixins`) import harmlessly.
"""
import importlib
import pkgutil

# Import every submodule in this package so their model classes register with
# Base.metadata. New model files are picked up automatically — no maintenance.
for _module in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_module.name}")
