from django.conf import settings

from tests import factories


workspaces = [w for w in settings.WORKSPACE_DIR.iterdir() if w.is_dir()]

for workspace in workspaces:
    factories.update_manifest(workspace.name)
    print(f"Writing manifest.json for workspace {workspace.name}")
