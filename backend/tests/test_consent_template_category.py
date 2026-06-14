"""ConsentTemplate.category (surgical|larc) — POST/PUT persistence,
filtered GET, and default. The `client` fixture is super-admin so it clears
the Surgery MANAGE gate on every endpoint.
"""


def _post(client, name, category=None):
    body = {
        "name": name,
        "boldsign_template_id": "bs-" + name.lower().replace(" ", "-"),
    }
    if category is not None:
        body["category"] = category
    r = client.post("/api/consent-templates", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_post_category_larc_persists_and_is_serialized(client):
    body = _post(client, "Nexplanon Consent", category="larc")
    assert body["category"] == "larc"


def test_post_without_category_defaults_surgical(client):
    body = _post(client, "Hysteroscopy Consent")
    assert body["category"] == "surgical"


def test_filtered_get_by_category(client):
    _post(client, "Mirena Consent", category="larc")
    _post(client, "D&C Consent", category="surgical")

    larc = client.get("/api/consent-templates", params={"category": "larc"})
    assert larc.status_code == 200, larc.text
    larc_names = {t["name"] for t in larc.json()}
    assert "Mirena Consent" in larc_names
    assert "D&C Consent" not in larc_names

    surgical = client.get("/api/consent-templates", params={"category": "surgical"})
    assert surgical.status_code == 200, surgical.text
    surgical_names = {t["name"] for t in surgical.json()}
    assert "D&C Consent" in surgical_names
    assert "Mirena Consent" not in surgical_names


def test_get_no_param_returns_all(client):
    _post(client, "LARC Form A", category="larc")
    _post(client, "Surgical Form B", category="surgical")

    r = client.get("/api/consent-templates")
    assert r.status_code == 200, r.text
    names = {t["name"] for t in r.json()}
    assert {"LARC Form A", "Surgical Form B"} <= names


def test_response_payload_includes_category(client):
    _post(client, "Kyleena Consent", category="larc")
    r = client.get("/api/consent-templates")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert rows, "expected at least one template"
    for t in rows:
        assert "category" in t
        assert t["category"] in {"surgical", "larc"}
