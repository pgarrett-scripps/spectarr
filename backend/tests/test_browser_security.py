import pytest


@pytest.mark.anyio
@pytest.mark.parametrize("headers", [
    {"Origin": "https://attacker.example"},
    {"Origin": "null"},
    {"Origin": "http://["},
    {"Host": "rebound.attacker.example"},
    {"Sec-Fetch-Site": "cross-site"},
])
async def test_browser_attack_is_rejected_before_mutation(client, headers):
    response = await client.post("/api/v1/projects", json={"name": "injected"}, headers=headers)
    assert response.status_code == 403
    assert (await client.get("/api/v1/projects")).json() == []


@pytest.mark.anyio
async def test_same_origin_and_native_requests_remain_available(client):
    response = await client.post("/api/v1/projects", json={"name": "local"}, headers={"Origin": "http://test"})
    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    assert (await client.get("/api/v1/projects")).status_code == 200


@pytest.mark.anyio
async def test_configured_origin_does_not_bypass_host_validation(client, monkeypatch):
    from spectarr.config import get_settings

    monkeypatch.setenv("SPECTARR_CORS_ORIGINS", '["https://trusted.example"]')
    get_settings.cache_clear()
    try:
        assert (await client.get("/api/v1/projects", headers={"Origin": "https://trusted.example"})).status_code == 200
        response = await client.get("/api/v1/projects", headers={"Origin": "https://trusted.example", "Host": "attacker.example"})
        assert response.status_code == 403
    finally:
        get_settings.cache_clear()


@pytest.mark.anyio
async def test_concurrent_bootstrap_creates_one_administrator(client, password_auth):
    import asyncio

    replies = await asyncio.gather(*[
        client.post('/api/v1/auth/bootstrap', json={'username': name, 'password': 'long-enough-test-password'})
        for name in ['first-admin', 'second-admin']
    ])
    assert sorted(response.status_code for response in replies) == [201, 409]
