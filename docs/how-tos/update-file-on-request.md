!!! note
    This page is out of date

## Updating files

When a file is added to a release request, a copy is taken of the current contents
of that file. This is deliberate, and ensures that a file added to a request
does not change during the review process.

If a subsequent job is run within that workspace which changes the file,
the workspace view will show the new file contents and the release request view
will continue to show the old file contents from the time that it was added to
the request.

To update a file, select the workspace, navigate to view the file,
and use "Add file to request" as usual. This will remove the old version of the file
from the request, and add the new version of the file.

!!!info "Updating files"
    Files can only be updated by the author of the request, and only during the Pending
    or Returned states.
    This means that if a request is in the Submitted or Reviewed states, the output
    checking team must return the request to the author in order for files to be updated.
    Updating a file will reset any reviews associated with that file.
