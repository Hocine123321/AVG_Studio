import pytest
from fastapi.testclient import TestClient
from avg.api.app import create_app

@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(token="t0k", output_root=tmp_path))

def test_requests_without_a_token_are_rejected(client):
    assert client.get("/api/projects").status_code == 401

def test_requests_with_the_token_succeed(client):
    r = client.get("/api/projects", headers={"X-AVG-Token": "t0k"})
    assert r.status_code == 200 and r.json() == {"projects": []}

def test_render_of_an_unknown_project_is_404(client):
    r = client.post("/api/projects/nope/render", headers={"X-AVG-Token": "t0k"})
    assert r.status_code == 404

def test_websocket_streams_events(client):
    with client.websocket_connect("/ws/events?token=t0k") as ws:
        assert ws.receive_json()["type"] == "hello"
