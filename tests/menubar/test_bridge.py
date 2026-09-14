from scanix500.menubar.bridge import ScanBusyError, route_scan_request
from scanix500.menubar.profiles import Profile
from scanix500.menubar.runner import ScanResult


class FakeScanTrigger:
    """Test double for bridge.ScanTrigger -- records the profile it was
    called with and returns/raises whatever the test configures, with no
    real threading, rumps, or AppKit involved."""

    def __init__(self, result: ScanResult | None = None, raises: Exception | None = None):
        self.result = result
        self.raises = raises
        self.called_with: Profile | None = None

    def trigger(self, profile: Profile) -> ScanResult:
        self.called_with = profile
        if self.raises is not None:
            raise self.raises
        return self.result


def test_route_scan_request_unknown_profile_returns_404():
    profiles = [Profile(name="Default", destination="/tmp/scans")]
    trigger = FakeScanTrigger()

    status, body = route_scan_request(profiles, "Nonexistent", trigger)

    assert status == 404
    assert "Nonexistent" in body["error"]
    assert trigger.called_with is None


def test_route_scan_request_busy_returns_409():
    profiles = [Profile(name="Dossiary Scan", destination="/tmp/scans")]
    trigger = FakeScanTrigger(raises=ScanBusyError())

    status, body = route_scan_request(profiles, "Dossiary Scan", trigger)

    assert status == 409
    assert "already in progress" in body["error"]


def test_route_scan_request_success_returns_200_with_scan_result_shape():
    profiles = [Profile(name="Dossiary Scan", destination="/tmp/scans")]
    result = ScanResult(ok=True, partial=False, message="/tmp/scans/scan_1.pdf", output_paths=["/tmp/scans/scan_1.pdf"])
    trigger = FakeScanTrigger(result=result)

    status, body = route_scan_request(profiles, "Dossiary Scan", trigger)

    assert status == 200
    assert body == {
        "ok": True,
        "partial": False,
        "message": "/tmp/scans/scan_1.pdf",
        "output_paths": ["/tmp/scans/scan_1.pdf"],
    }
    assert trigger.called_with == profiles[0]


def test_route_scan_request_partial_result_still_returns_200():
    profiles = [Profile(name="Dossiary Scan Multi", destination="/tmp/scans", split_on_blank=True)]
    result = ScanResult(
        ok=False,
        partial=True,
        message="Multi-feed detected at sheet 3 — partial scan saved to /tmp/scans/scan_1.pdf",
        output_paths=["/tmp/scans/scan_1.pdf"],
    )
    trigger = FakeScanTrigger(result=result)

    status, body = route_scan_request(profiles, "Dossiary Scan Multi", trigger)

    assert status == 200
    assert body["ok"] is False
    assert body["partial"] is True
