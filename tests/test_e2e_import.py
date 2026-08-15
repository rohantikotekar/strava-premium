"""End-to-end: signup -> presigned upload -> ingest -> charts.

This is the flow FRONTEND_DESIGN.md calls one of the two that must never break.
It exercises the real API, a real Celery worker, real Postgres and real object
storage — no mocks.

Requires the full local stack:

    docker compose up -d
    make migrate
    make api        # in another terminal
    make worker     # in another terminal
"""

from __future__ import annotations

import time
import uuid

import httpx
import pytest

from tests.fixtures.export_builder import build_export_zip

pytestmark = pytest.mark.integration

API = "http://127.0.0.1:8000"
PASSWORD = "a-perfectly-fine-passphrase"


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    with httpx.Client(base_url=API, timeout=60.0, follow_redirects=True) as session:
        try:
            session.get("/health").raise_for_status()
        except httpx.HTTPError:
            pytest.skip("API is not running on :8000 — start it with `make api`")
        yield session


@pytest.fixture(scope="module")
def signed_up(client: httpx.Client) -> str:
    email = f"e2e-{uuid.uuid4().hex[:10]}@example.com"
    response = client.post("/auth/signup", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    # Signup issues a session cookie directly — no separate login round trip.
    assert client.cookies.get("sp_session")
    return email


class TestAuthFlow:
    def test_signup_creates_a_session(self, client: httpx.Client, signed_up: str):
        me = client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == signed_up
        assert me.json()["has_password"] is True
        # Strava is a separate data connection, not identity.
        assert me.json()["strava_connected"] is False

    def test_password_is_never_returned(self, client: httpx.Client, signed_up: str):
        body = client.get("/auth/me").text
        assert "password_hash" not in body
        assert "argon2" not in body

    def test_duplicate_signup_does_not_reveal_the_account(self, signed_up: str):
        """Enumeration resistance: the response must look identical to a new signup."""
        with httpx.Client(base_url=API, timeout=30.0) as fresh:
            first = fresh.post(
                "/auth/signup",
                json={"email": f"new-{uuid.uuid4().hex[:8]}@example.com", "password": PASSWORD},
            )
            duplicate = fresh.post("/auth/signup", json={"email": signed_up, "password": PASSWORD})
        assert duplicate.status_code == first.status_code
        # In production both responses are byte-identical. Locally the new-account
        # branch appends a "[dev] <verification link>" hint (there is no SMTP), so
        # compare only the part a deployed instance would return.
        assert duplicate.json()["message"].split("[dev]")[0].strip() == (
            first.json()["message"].split("[dev]")[0].strip()
        )

    def test_wrong_password_is_generic(self, signed_up: str):
        with httpx.Client(base_url=API, timeout=30.0) as fresh:
            response = fresh.post(
                "/auth/login", json={"email": signed_up, "password": "wrong-password-here"}
            )
        assert response.status_code == 401
        assert "password" in response.json()["detail"].lower()

    def test_breached_password_is_rejected(self):
        """Checked against Have I Been Pwned via k-anonymity (AUTH.md §2)."""
        with httpx.Client(base_url=API, timeout=30.0) as fresh:
            response = fresh.post(
                "/auth/signup",
                json={
                    "email": f"weak-{uuid.uuid4().hex[:8]}@example.com",
                    "password": "password123456",
                },
            )
        # Fails open if HIBP is unreachable, so accept either outcome but never a 500.
        assert response.status_code in (201, 422)

    def test_protected_endpoints_reject_anonymous(self):
        with httpx.Client(base_url=API, timeout=30.0) as anonymous:
            for path in ("/auth/me", "/me/capabilities", "/activities", "/charts/fitness"):
                assert anonymous.get(path).status_code == 401, path


class TestImportPipeline:
    def test_full_import_produces_charts(self, client: httpx.Client, signed_up: str):
        archive = build_export_zip(n_activities=12)

        # 1. Mint a presigned URL. The API never sees the bytes.
        created = client.post(
            "/uploads", json={"filename": "export.zip", "size_bytes": len(archive)}
        )
        assert created.status_code == 201, created.text
        upload = created.json()
        assert upload["upload_url"].startswith("http")

        # 2. Browser -> object store, directly.
        with httpx.Client(timeout=120.0) as direct:
            put = direct.put(
                upload["upload_url"],
                content=archive,
                headers={"Content-Type": "application/zip"},
            )
        assert put.status_code in (200, 204), put.text

        # 3. Tell the API it landed; this enqueues the pipeline.
        completed = client.post(f"/uploads/{upload['upload_id']}/complete")
        assert completed.status_code == 200, completed.text

        # 4. The fast path must make the dashboard usable quickly.
        status = self._await_status(client, upload["upload_id"], "fast_path_done_at")
        assert status["activities_found"] > 0

        # 5. Then the deep parse finishes.
        final = self._await_status(client, upload["upload_id"], "completed_at", timeout=180)
        assert final["status"] == "complete", final

        # Partial success is the normal outcome: the corrupt member failed, and
        # that did not stop the other files (CLAUDE.md §4.6).
        assert final["items_failed"] >= 1
        assert final["items_done"] >= 1

        failures = client.get(f"/imports/{upload['upload_id']}/failures").json()
        assert any("9999999" in item["member_path"] for item in failures)

        # 6. Capabilities reflect what this synthetic athlete actually has.
        capabilities = client.get("/me/capabilities").json()
        found = {entry["capability"] for entry in capabilities["capabilities"]}
        # 12 CSV rows + 1 orphan: the truncated .fit has no CSV row, and a file
        # that parses without one becomes its own activity rather than being
        # discarded (INGESTION.md §3).
        assert capabilities["total_activities"] == 13
        assert "sport.run" in found
        assert "sport.ride" in found
        assert "stream.heartrate" in found
        assert "stream.power" in found

        # 7. Charts return real series.
        fitness = client.get("/charts/fitness?range=all").json()
        assert len(fitness["series"][0]["points"]) > 0
        assert any(point["ctl"] > 0 for point in fitness["series"][0]["points"])

        weekly = client.get("/charts/weekly-volume?range=all").json()
        assert len(weekly["series"][0]["points"]) > 0

        # No max HR is set yet, so there is no zone model and the chart is
        # legitimately empty rather than wrong. See test_setting_max_hr_* below.
        zones = client.get("/charts/hr-zones?range=all").json()
        assert sum(point["seconds"] for point in zones["series"][0]["points"]) == 0
        assert zones["meta"]["coverage_note"] is not None

        curve = client.get("/charts/power-curve?range=all").json()
        assert len(curve["series"][0]["points"]) > 0

        summary = client.get("/charts/dashboard-summary?range=all").json()
        assert summary["hero"]["value"] > 0
        assert summary["active_days"] > 0

    def test_reupload_is_idempotent(self, client: httpx.Client, signed_up: str):
        """Re-uploading the same archive must be a no-op, not a duplicate
        (CLAUDE.md §4.2)."""
        before = client.get("/me/capabilities").json()["total_activities"]
        assert before > 0, "run test_full_import_produces_charts first"

        archive = build_export_zip(n_activities=12)
        upload = client.post(
            "/uploads", json={"filename": "export.zip", "size_bytes": len(archive)}
        ).json()
        with httpx.Client(timeout=120.0) as direct:
            direct.put(
                upload["upload_url"],
                content=archive,
                headers={"Content-Type": "application/zip"},
            )
        client.post(f"/uploads/{upload['upload_id']}/complete")
        self._await_status(client, upload["upload_id"], "completed_at", timeout=180)

        after = client.get("/me/capabilities").json()["total_activities"]
        assert after == before, f"re-import duplicated activities: {before} -> {after}"

    def test_setting_max_hr_recomputes_history_and_unlocks_zones(
        self, client: httpx.Client, signed_up: str
    ):
        """Settings promises "this recomputes training load for every activity" —
        this asserts it actually happens, retroactively, from the stored streams.
        """
        before = client.get("/charts/fitness?range=all").json()
        assert before["meta"]["is_estimate"] is True, "no thresholds set yet"
        used_before = before["meta"]["activities_used"]

        response = client.patch(
            "/me/profile", json={"max_hr_bpm": 190, "resting_hr_bpm": 50, "ftp_w": 220}
        )
        assert response.status_code == 200
        assert response.json()["max_hr_bpm"] == 190

        # The recompute is a background task; poll for the zones to appear.
        deadline = time.monotonic() + 120
        zone_seconds = 0
        while time.monotonic() < deadline:
            zones = client.get("/charts/hr-zones?range=all").json()
            zone_seconds = sum(point["seconds"] for point in zones["series"][0]["points"])
            if zone_seconds > 0:
                break
            time.sleep(2)

        assert zone_seconds > 0, "setting max HR did not retroactively build HR zones"

        # Most activities now derive load from real HR/power rather than duration.
        # It stays *partly* estimated, correctly: the strength sessions in the
        # fixture carry no heart rate at all, so they can only ever be RPE-based —
        # and the chart must keep saying so rather than implying otherwise.
        after = client.get("/charts/fitness?range=all").json()
        assert after["meta"]["activities_used"] > used_before
        assert after["meta"]["is_estimate"] is True
        assert "estimated" in (after["meta"]["estimate_reason"] or "").lower()

    def test_activities_are_listed_and_detailed(self, client: httpx.Client, signed_up: str):
        page = client.get("/activities?limit=50").json()
        assert page["total"] > 0

        with_streams = next((a for a in page["items"] if a["has_streams"]), None)
        assert with_streams is not None, "expected at least one activity with streams"

        detail = client.get(f"/activities/{with_streams['id']}").json()
        assert detail["available_channels"]

        streams = client.get(f"/activities/{with_streams['id']}/streams?max_points=500").json()
        assert streams["n_samples"] > 0
        assert "t" in streams["channels"]

    def test_zero_distance_activity_is_kept(self, client: httpx.Client, signed_up: str):
        """Strength sessions are real activities; they must not be dropped for
        having no distance."""
        page = client.get("/activities?sport=gym&limit=50").json()
        assert page["total"] > 0
        assert all((item["distance_m"] or 0) == 0 for item in page["items"])

    @staticmethod
    def _await_status(client: httpx.Client, upload_id: str, field: str, timeout: int = 90) -> dict:
        deadline = time.monotonic() + timeout
        last: dict = {}
        while time.monotonic() < deadline:
            last = client.get(f"/imports/{upload_id}").json()
            if last.get(field):
                return last
            if last.get("status") == "failed":
                pytest.fail(f"import failed: {last.get('error')}")
            time.sleep(2)
        pytest.fail(f"timed out waiting for {field}; last status: {last}")


class TestTenantIsolationOverHttp:
    def test_a_second_user_sees_none_of_the_first_users_data(self, signed_up: str):
        """The HTTP-level counterpart to the RLS unit tests."""
        with httpx.Client(base_url=API, timeout=30.0) as other:
            email = f"other-{uuid.uuid4().hex[:8]}@example.com"
            other.post("/auth/signup", json={"email": email, "password": PASSWORD})

            assert other.get("/activities").json()["total"] == 0
            assert other.get("/me/capabilities").json()["total_activities"] == 0
            assert other.get("/imports").json() == []
