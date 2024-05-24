from django.conf import settings

from tests import factories


workspaces = [w for w in settings.WORKSPACE_DIR.iterdir() if w.is_dir()]

for workspace in workspaces:
    print(f"Writing manifest.json and creating repo for workspace {workspace.name}")
    repo = factories.create_repo(workspace.name, temporary=False)
