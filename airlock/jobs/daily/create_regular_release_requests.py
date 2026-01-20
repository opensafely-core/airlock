import json
import logging

from django.conf import settings
from django_extensions.management.jobs import DailyJob
from opentelemetry import trace

from airlock import actions


logger = logging.getLogger(__name__)

CONFIG_PATH = settings.WORK_DIR / "regular_release_requests.json"

CONFIG_TYPES = {
    "username": str | None,
    "workspace_name": str,
    "dirs": list,
    "context": str,
    "controls": str,
    "supporting_files": list,
    "submit": bool,
}


class ConfigValidationError(Exception): ...


class Job(DailyJob):
    help = "Create release requests for regularly run jobs."

    def execute(self):
        tracer = trace.get_tracer("scheduled_commands")
        release_requests = get_config_data()
        for release_request in release_requests:
            logger.info(
                f"Starting automated release request for {release_request['workspace_name']}"
            )
            # Don't trace context/controls
            attributes_to_trace = {
                k: v
                for k, v in release_request.items()
                if k not in ["context", "controls"]
            }
            with tracer.start_as_current_span(
                "create_regular_release_requests",
                attributes=attributes_to_trace,
            ) as span:
                kwargs = {
                    k: v
                    for k, v in release_request.items()
                    if k not in ["username", "workspace_name"]
                }
                try:
                    validate_config_data(release_request)
                    result = actions.create_release_request(
                        release_request["username"],
                        release_request["workspace_name"],
                        **kwargs,
                    )
                    span.set_attributes({f"result.{k}": v for k, v in result.items()})
                    if result["completed"]:
                        logger.info(
                            "Release request complete for %s: %s",
                            release_request["workspace_name"],
                            result["request_id"],
                        )
                    else:
                        logger.info(
                            "Release request creation not completed for %s: %s",
                            release_request["workspace_name"],
                            result["message"],
                        )
                except Exception as error:
                    span.record_exception(error)
                    logger.error(
                        "Failed to create release request for %s - %s",
                        release_request["workspace_name"],
                        str(error),
                    )


def get_config_data():
    if not CONFIG_PATH.exists():
        return []
    return json.loads(CONFIG_PATH.read_text())


def validate_config_data(release_request_data):
    """Ensure the loaded config data has required keys and it's in the right shape"""
    errors = []
    # required keys are present
    missing_required_keys = {"username", "workspace_name", "dirs"} - set(
        release_request_data.keys()
    )

    if missing_required_keys:
        errors.append(
            "keys missing in config: " + ",".join(sorted(missing_required_keys))
        )

    # check types
    for key, value in release_request_data.items():
        # Every config key should have a type explicitly specified (and there's a test to ensure that all
        # possible parameters passed to create_release_request are included in CONFIG_TYPES). If the key
        # isn't there, there's some error in the config.
        expected_type = CONFIG_TYPES.get(key)
        if expected_type is None:
            errors.append(f"Unknown config key: '{key}'")
        elif not isinstance(value, expected_type):
            errors.append(
                f"Invalid config type for '{key}': expected {expected_type}, got {type(value)}"
            )

    # If the release request is to be submitted, context and controls must also be provided
    if release_request_data.get("submit") and not (
        release_request_data.get("context") and release_request_data.get("controls")
    ):
        errors.append("Context and/or controls missing")

    if errors:
        raise ConfigValidationError("; ".join(errors))
