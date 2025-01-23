A **workspace file** is always the latest version of the file created by
a job run via the Jobs site.

A **request file** is the version of the file at the time that it was
added to a release request.

When a file is added to a release request, a copy is taken of the current contents
of that file. This is deliberate, and ensures that a file added to a request
does not change during the review process.

If a subsequent job is run within that workspace which changes the file,
the workspace view will show the new file contents and the release request view
will continue to show the old file contents from the time that it was added to
the request. Authors can choose to [update the file](../how-tos/edit-file-on-request.md#update-a-file)
on the release request, but this does not happen automatically.

After a file has been updated on a release request, it must be reviewed again by
two output checkers, even if the previous version of the file was already
approved.
