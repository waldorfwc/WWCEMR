def test_create_user_with_clinician_fields_shows_in_clinicians(client, db):
    r = client.post("/api/admin/users", json={
        "email": "acooke@waldorfwomenscare.com", "group": "clinical",
        "display_name": "Aryian Cooke", "npi": "1234567890",
        "clinician_role": "provider", "credential": "MD"})
    assert r.status_code in (200, 201), r.text
    clinicians = client.get("/api/admin/users/clinicians").json()
    match = [c for c in clinicians if c["email"] == "acooke@waldorfwomenscare.com"]
    assert match, "new provider not in clinicians list"
    assert match[0]["npi"] == "1234567890"
    assert match[0]["clinician_role"] == "provider"
    assert match[0]["credential"] == "MD"
