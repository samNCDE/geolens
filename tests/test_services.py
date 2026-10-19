"""The service check reports each service, and a failure does not hide the
others."""
from geolens import services


def test_each_service_is_reported(monkeypatch):
    def ok(timeout):
        return "fine"

    def down(timeout):
        raise OSError("Tunnel connection failed: 403")

    monkeypatch.setattr(services, "CHECKS", [("A", "x", ok), ("B", "y", down),
                                             ("C", "z", ok)])
    r = services.check_services(timeout=1)
    assert r["ok"] is False and r["failed"] == ["B"]
    assert [s["ok"] for s in r["services"]] == [True, False, True]
    assert "403" in r["services"][1]["detail"]


def test_every_check_names_what_uses_it():
    assert len(services.CHECKS) >= 8
    assert all(name and used for name, used, _ in services.CHECKS)
