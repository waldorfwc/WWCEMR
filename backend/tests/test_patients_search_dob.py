"""Search by DOB on /api/documents/patients."""
from datetime import date
from app.models.patient_directory import PatientDirectory
from app.models.document import PatientDocument


def test_search_by_dob_iso(client, db):
    # Two patients with different DOBs. Seed a doc for each so list_patients shows them.
    db.merge(PatientDirectory(chart_number="CA", patient_name="Alpha, A", dob=date(1985, 2, 14)))
    db.merge(PatientDirectory(chart_number="CB", patient_name="Beta, B", dob=date(1990, 8, 1)))
    db.add_all([
        PatientDocument(chart_number="CA", doc_type="x", doc_id="1",
                        filename="a.pdf", file_path="/tmp/a.pdf"),
        PatientDocument(chart_number="CB", doc_type="x", doc_id="2",
                        filename="b.pdf", file_path="/tmp/b.pdf"),
    ])
    db.commit()

    r = client.get("/api/documents/patients?search=1985-02-14")
    assert r.status_code == 200
    body = r.json()
    charts = {p["chart_number"] for p in body["patients"]}
    assert charts == {"CA"}


def test_search_by_partial_dob(client, db):
    db.merge(PatientDirectory(chart_number="Y1", patient_name="Y, A", dob=date(1985, 2, 14)))
    db.merge(PatientDirectory(chart_number="Y2", patient_name="Y, B", dob=date(1985, 7, 22)))
    db.merge(PatientDirectory(chart_number="Y3", patient_name="Y, C", dob=date(1992, 1, 1)))
    db.add_all([
        PatientDocument(chart_number=c, doc_type="x", doc_id="1",
                        filename=f"{c}.pdf", file_path=f"/tmp/{c}.pdf")
        for c in ("Y1", "Y2", "Y3")
    ])
    db.commit()

    # Partial year-only match
    r = client.get("/api/documents/patients?search=1985")
    charts = {p["chart_number"] for p in r.json()["patients"]}
    assert charts == {"Y1", "Y2"}
