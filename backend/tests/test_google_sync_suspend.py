"""Google sync: deactivating a user must bump token_version so their live
session is revoked (clean 401 logout), instead of leaving a zombie session
that 403s on every request."""
import app.services.google_sync as gs
from app.models.user import User


def _gu(email, *, suspended=False, archived=False, full_name="A B"):
    return {"email": email, "suspended": suspended, "archived": archived,
            "full_name": full_name}


def _user(db, email, *, active=True, tv=0):
    u = User(email=email, display_name=email.split("@")[0], is_active=active,
             auto_provisioned=True, token_version=tv)
    db.add(u); db.commit()
    return u


def test_suspend_in_directory_bumps_token_version(db, monkeypatch):
    u = _user(db, "leaver@waldorfwomenscare.com", active=True, tv=0)
    monkeypatch.setattr(gs, "is_configured", lambda: True)
    monkeypatch.setattr(gs, "list_workspace_users",
                        lambda: [_gu("leaver@waldorfwomenscare.com", suspended=True)])
    gs.run_sync(db)
    db.refresh(u)
    assert u.is_active is False
    assert u.token_version == 1


def test_not_in_directory_bumps_token_version(db, monkeypatch):
    u = _user(db, "ghost@waldorfwomenscare.com", active=True, tv=3)
    monkeypatch.setattr(gs, "is_configured", lambda: True)
    monkeypatch.setattr(gs, "list_workspace_users", lambda: [])   # gone from Google
    gs.run_sync(db)
    db.refresh(u)
    assert u.is_active is False
    assert u.token_version == 4


def test_reactivation_does_not_bump_token_version(db, monkeypatch):
    u = _user(db, "back@waldorfwomenscare.com", active=False, tv=2)
    monkeypatch.setattr(gs, "is_configured", lambda: True)
    monkeypatch.setattr(gs, "list_workspace_users",
                        lambda: [_gu("back@waldorfwomenscare.com", suspended=False)])
    gs.run_sync(db)
    db.refresh(u)
    assert u.is_active is True
    assert u.token_version == 2   # coming back online must not revoke a session
