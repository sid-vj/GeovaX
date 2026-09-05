"""Unit tests for the real government connectors added for NAKSHA, SoI CORS, and TN Revenue
(Patta/Chitta/FMB via TamilNilam).

These deliberately never hit the live government endpoints during the normal test run (that
would make CI depend on external, credential-gated systems that are supposed to fail without
credentials) — they test the one thing that must always be true regardless of network state:
with no credentials configured, every connector honestly reports `credential_required` and
returns zero fabricated records. The connectors' real HTTP logic was separately proven this
session by hand against the live endpoints (see this session's compliance report / the
modules' own docstrings for the exact real HTTP responses observed), which is not something a
unit test should silently re-run against a production government server on every commit.
"""
import os

import pytest

from samanvay.ingest.naksha import NakshaConnector, TN_FEATURE_SERVERS
from samanvay.ingest.soi_cors import SOICorsConnector
from samanvay.ingest.tn_revenue import TNRevenueConnector


@pytest.fixture(autouse=True)
def _clear_credential_env(monkeypatch):
    for var in ("NAKSHA_USERNAME", "NAKSHA_PASSWORD", "SOI_CORS_USERNAME", "SOI_CORS_PASSWORD",
                "TN_REVENUE_USERNAME", "TN_REVENUE_PASSWORD", "TN_REVENUE_SESSION_COOKIE"):
        monkeypatch.delenv(var, raising=False)


def test_naksha_unconfigured_reports_credential_required():
    conn = NakshaConnector()
    assert not conn.configured
    token, detail = conn.generate_token()
    assert token is None
    assert "credential_required" in detail


def test_naksha_fetch_parcels_without_token_never_fabricates():
    result = NakshaConnector().fetch_parcels()
    assert result.data_source == "credential_required"
    assert result.parcels == []


def test_naksha_unknown_utm_zone_is_a_real_error_not_a_silent_empty():
    result = NakshaConnector(username="x", password="y").fetch_parcels(utm_zone=99)
    assert result.data_source == "error"
    assert "99" in result.detail


def test_naksha_tn_feature_servers_cover_both_real_utm_zones():
    # Tamil Nadu genuinely spans UTM 43N (west) and 44N (east, incl. Chennai) — both real
    # NAKSHA services were found and probed live this session.
    assert set(TN_FEATURE_SERVERS) == {43, 44}
    assert "Naksha_tn_33_44" in TN_FEATURE_SERVERS[44]


def test_naksha_credentials_from_explicit_args_not_only_env():
    conn = NakshaConnector(username="dept_user", password="dept_pass")
    assert conn.configured


def test_soi_cors_unconfigured_reports_credential_required():
    conn = SOICorsConnector()
    assert not conn.configured
    result = conn.fetch_rinex("CHEN", "2026-01-01", "2026-01-02")
    assert result.data_source == "credential_required"
    assert result.observations == []


def test_soi_cors_paths_are_overridable_without_code_changes(monkeypatch):
    monkeypatch.setenv("SOI_CORS_LOGIN_PATH", "/api/v2/auth/login")
    monkeypatch.setenv("SOI_CORS_RDS_PATH", "/api/v2/rds/download")
    conn = SOICorsConnector(username="u", password="p")
    assert conn.login_path == "/api/v2/auth/login"
    assert conn.rds_path == "/api/v2/rds/download"


def test_tn_revenue_unconfigured_reports_credential_required():
    conn = TNRevenueConnector()
    assert not conn.configured
    result = conn.fetch_patta_chitta("Kilpauk", "145")
    assert result.data_source == "credential_required"
    assert result.records == []


def test_tn_revenue_session_cookie_alone_counts_as_configured():
    conn = TNRevenueConnector(session_cookie="JSESSIONID=abc123")
    assert conn.configured


def test_tn_revenue_never_matches_a_different_survey_number(monkeypatch):
    # Guards the "never infer a property linkage from proximity" rule at the connector
    # boundary: even if a backend returned some other record, this connector only accepts
    # it when the record's own survey_number field equals the one asked for.
    conn = TNRevenueConnector(session_cookie="fake")

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"record": {"survey_number": "999", "village": "Kilpauk"}}

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **kw):
            return _FakeResponse()

    import samanvay.ingest.tn_revenue as tn_revenue_mod

    class _FakeHttpx:
        @staticmethod
        def Client(*a, **kw):
            return _FakeSession()

    monkeypatch.setitem(__import__("sys").modules, "httpx", _FakeHttpx())
    result = conn.fetch_patta_chitta("Kilpauk", "145")
    assert result.data_source == "live"
    assert result.records == []
