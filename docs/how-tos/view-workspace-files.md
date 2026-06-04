To view files in a workspace, navigate to the 'Workspaces & Requests' view using the links in the
navigation bar. This will show you a list of workspaces that you have access to, organised
by project.

![Workspaces index](../screenshots/workspaces_index.png)

Click on a workspace to view its files. The landing page for a workspace shows some details of the workspace and project. On the left hand side the workspace files are displayed in a
browsable file tree.

![Workspace view](../screenshots/workspace_view.png)

The `metadata/` directory contains job log files. These contain the output your
code generated when it last ran, and are useful for debugging issues.

!!!note "Log files may be truncated"
     To reduce risk of accidental disclosure of pseudonymous patient level
     data, only the last few hundred lines of a log file are displayed in
     Airlock. This should allow debugging of any issues. Contact tech support
     if you need access to the full log.

![Log files in Metadata](../screenshots/workspace_view_metadata.png)

Clicking on a directory will display a list of the files it contains, with some metadata
about the file, such as size, type and last modified date.

![Workspace directory view](../screenshots/workspace_directory_view.png)

To view a specific file, click on it in the directory view, or in the file tree. The contents
of the file will be displayed.

![Workspace file view](../screenshots/workspace_file_view.png)

For CSV files, some [summary statistics](../reference/csv-summary.md) can be viewed by clicking on the "View summary stats" toggle.

From the file view, the `More` dropdown also allows you to [view the file in alternative ways](../reference/view-files-alt.md), or to [view the source code](../reference/view-source-code.md) underlying
the file.

![More dropdown](../screenshots/more_dropdown_el.png)

## Outputs from out-of-date actions

Some outputs may have been produced by an action that no longer exists in the `project.yaml`.
These are hidden from the file tree by default to keep your workspace uncluttered.
A notice at the top of the tree shows how many such outputs exist.

![Outputs from out-of-date actions hidden](../screenshots/workspace_out_of_date_hidden.png)

Click **show** to reveal them. Outputs from out-of-date actions appear with a warning icon and
greyed-out styling to distinguish them from current outputs.

![Outputs from out-of-date actions shown](../screenshots/workspace_out_of_date_shown.png)

Click **hide** to hide them again.
