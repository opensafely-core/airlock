def test_components_browser(client):
    response = client.get("/ui-components/")
    assert response.status_code == 200
