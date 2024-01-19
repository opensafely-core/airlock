def test_index(client):
    response = client.get("/")
    assert "Hello World" in response.rendered_content
