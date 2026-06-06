"""Catalog tests: Module + Tier enums and MODULE_REGISTRY coverage."""
from app.permissions.catalog import MODULE_REGISTRY, Module, Tier


def test_module_enum_has_all_15_modules():
    expected = {
        "chart", "active_ar", "billing_bank_recon", "billing_missing_charges",
        "billing_insurance_docs", "billing_insurance_contacts", "recall",
        "surgery", "device_larc", "device_office_procedures", "pellets",
        "reputation", "training", "my_checklist", "audit_log",
    }
    assert {m.value for m in Module} == expected


def test_tier_ordinal_values():
    assert Tier.NONE < Tier.VIEW < Tier.WORK < Tier.MANAGE < Tier.ADMIN < Tier.SUPER_ADMIN
    assert Tier.NONE == 0
    assert Tier.VIEW == 10
    assert Tier.SUPER_ADMIN == 50


def test_module_registry_covers_every_module():
    for m in Module:
        spec = MODULE_REGISTRY[m]
        assert spec.label
        assert spec.manage_means
