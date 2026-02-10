import pytest
from django.core.management import call_command

from local_db.models import RequestMetadata
from tests import factories
from users.auth import Level4AuthenticationBackend
from users.models import User


@pytest.mark.django_db
def test_command(bll, auth_api_stubber):
    # User with old auth api
    author = factories.create_airlock_user(
        username="testuser",
        workspaces={
            "workspace": {
                "project_details": {"name": "Project 1", "ongoing": True},
                "archived": False,
            },
            "workspace1": {
                "project_details": {"name": "Project 2", "ongoing": True},
                "archived": False,
            },
        },
    )
    # Mock response from auth endpoint with new workspace data including orgs
    auth_responses = auth_api_stubber(
        "authorise",
        json={
            "username": "testuser",
            "output_checker": False,
            "workspaces": {
                "workspace": {
                    "archived": False,
                    "project_details": {
                        "name": "Project 1",
                        "ongoing": True,
                        "orgs": ["Organisation 1"],
                    },
                },
                "workspace1": {
                    "archived": False,
                    "project_details": {
                        "name": "Project 2",
                        "ongoing": True,
                        "orgs": ["Organisation 2"],
                    },
                },
            },
        },
    )

    release_request = factories.create_release_request("workspace", user=author)
    release_request1 = factories.create_release_request("workspace1", user=author)
    # Set release request project/orgs to "" to replicate state after initial migration
    for request_from_db in RequestMetadata.objects.all():
        request_from_db.project = ""
        request_from_db.organisations = ""
        request_from_db.save()

    release_request = factories.refresh_release_request(release_request)
    release_request1 = factories.refresh_release_request(release_request1)
    for req in [release_request, release_request1]:
        assert release_request.project == ""
        assert release_request.organisations == []

    call_command("backpopulate_project_and_orgs_on_request_metadata")

    release_request = factories.refresh_release_request(release_request)
    release_request1 = factories.refresh_release_request(release_request1)
    assert release_request.project == "Project 1"
    assert release_request.organisations == ["Organisation 1"]
    assert release_request1.project == "Project 2"
    assert release_request1.organisations == ["Organisation 2"]

    # Author has 2 requests, but we only call the auth API once
    assert len(auth_responses.calls) == 1


@pytest.mark.django_db
def test_command_no_user_in_db(bll, auth_api_stubber):
    author = factories.create_airlock_user(
        username="testuser",
        workspaces={
            "workspace": {
                "project_details": {"name": "Project 1", "ongoing": True},
                "archived": False,
            }
        },
    )
    # Mock response from auth endpoint with new workspace data including orgs
    auth_api_stubber(
        "authorise",
        json={
            "username": "testuser",
            "output_checker": False,
            "workspaces": {
                "workspace": {
                    "archived": False,
                    "project_details": {
                        "name": "Project 1",
                        "ongoing": True,
                        "orgs": ["Organisation 1"],
                    },
                }
            },
        },
    )

    release_request = factories.create_release_request("workspace", user=author)

    # Set release request project/orgs to "" to replicate state after initial migration
    request_from_db = RequestMetadata.objects.get(id=release_request.id)
    request_from_db.project = ""
    request_from_db.organisations = ""
    request_from_db.save()

    # delete the author to mock a release request create before the User model existed
    author.delete()
    assert Level4AuthenticationBackend().get_user(request_from_db.author) is None

    call_command("backpopulate_project_and_orgs_on_request_metadata")

    release_request = factories.refresh_release_request(release_request)
    assert release_request.project == "Project 1"
    assert release_request.organisations == ["Organisation 1"]

    assert release_request.author == User.objects.get(user_id="testuser")


@pytest.mark.django_db
def test_create_release_request_api_auth_error(bll, auth_api_stubber, capsys):
    auth_api_stubber("authorise", status=400)

    # User with old auth api
    author = factories.create_airlock_user(
        username="testuser",
        workspaces={
            "workspace": {
                "project_details": {"name": "Project 1", "ongoing": True},
                "archived": False,
            }
        },
    )

    release_request = factories.create_release_request("workspace", user=author)
    assert release_request.project == "Project 1"
    assert release_request.organisations == []

    call_command("backpopulate_project_and_orgs_on_request_metadata")
    output = capsys.readouterr().out
    assert f"Error updating request {release_request.id}" in output

    release_request = factories.refresh_release_request(release_request)
    assert release_request.project == "Project 1"
    assert release_request.organisations == []
