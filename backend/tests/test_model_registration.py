"""Guards the model-registration hardening: importing `app.models` must
auto-import every model submodule and register every table in Base.metadata,
so a refactor can't silently drop a table on a fresh DB.
"""
import sys
import pkgutil

import app.models
from app.database import Base


def test_every_model_submodule_is_auto_imported():
    """Importing the package imports all of its submodules — so a newly added
    model file is registered automatically (no hand-maintained list to forget)."""
    for mod in pkgutil.iter_modules(app.models.__path__):
        assert f"app.models.{mod.name}" in sys.modules, (
            f"app.models.{mod.name} was not auto-imported — model registration "
            f"would be incomplete")


def test_previously_fragile_tables_are_registered():
    """Tables that used to register only via incidental router/backref imports
    must now be present purely from importing app.models."""
    expected = {
        "active_claims",          # active_ar
        "appeal_letters",         # appeal_letters
        "insurance_contacts",     # insurance_contact
        "reputation_profiles",    # reputation
        "stripe_customers",       # stripe_payment
        "surgery_config",         # surgery_config
        "surgery_messages",       # surgery_message
    }
    missing = expected - set(Base.metadata.tables)
    assert not missing, f"tables not registered from app.models import: {missing}"
