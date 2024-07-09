from django.conf import settings

from airlock.business_logic import Workspace
from tests import factories


workspaces = [w for w in settings.WORKSPACE_DIR.iterdir() if w.is_dir()]

for workspace in workspaces:
    try:
        workspace = Workspace.from_directory(workspace.name)
        first_output = next(
            (output for output in workspace.manifest["outputs"].values()), None
        )
        is_real_repo = first_output and first_output["repo"].startswith(
            "https://github.com"
        )
    except Exception:
        is_real_repo = False

    if is_real_repo:
        # If it's a real github repo, just update the manifest
        print(f"Updating manifest.json for workspace {workspace.name}")
        factories.update_manifest(workspace.name)
    else:
        print(f"Writing manifest.json and creating repo for workspace {workspace.name}")
        repo = factories.create_repo(workspace.name, temporary=False)
