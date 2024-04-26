import requests
from django.conf import settings


session = requests.Session()


def send_notification_event(event_json: str, username: str):
    """API call to job server to create a release."""
    if not settings.AIRLOCK_API_TOKEN:
        # Skip attempting to send notifications when we're running a
        # local dev server in isolation
        return {"status": "ok"}
    response = session.post(
        url=f"{settings.AIRLOCK_API_ENDPOINT}/airlock/events/",
        data=event_json,
        headers={
            "OS-User": username,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": settings.AIRLOCK_API_TOKEN,
        },
    )

    # We expect to get a 201 back from job-server, even if it encountered an error in
    # processing the notification. If we get anything else, return it in the expected
    # format so it can be logged
    if response.status_code != 201:
        return {
            "status": "error",
            "message": f"Error sending notification: {response.status_code} {response.reason}",
        }
    return response.json()
