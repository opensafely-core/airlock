!!! note
    This page is out of date

After logging into Airlock, researchers can view files in workspaces that they
have permission to access.

Researchers populate a release request by adding, and if necessary withdrawing
files.

## Adding files

To add a file, researchers select the relevant workspace, navigate to view the file, and
use "Add File to Request". Airlock will ask the researcher to specify the type of
file and the file group that the file should be added to.

### File types

When adding a file, researchers should choose one of these two options:

- **Output** Files of this type contain the data the researcher wishes to be
  released. These files will ultimately be released if approved.
- **Supporting** Files of this type contain supplementary data to support the
  review of "output" type files in the release request, e.g. the underlying data used to generate a figure. These files will ultimately not be released.

### File groups

File groups allow the researcher to group the various files in a release request
into logical groups, in order to help the output checker understand the request.
Supporting files should be placed in the same file group as the Output file they support. 

## Adding context and controls

Context and controls should be added to each file group. These allow researchers to
provide information about the files requested for release. Files should be organised
into groups that share the same context and controls so that this information only
needs to be provided once per group of files.

* Context: infomation about what data is contained in the files in the file group.
* Controls: information about what disclosure controls (e.g. rounding/suppression) have been applied.

To add context and controls to a group, the researcher should navigate to the current
release request and click on the name of the relevant group. This will open a page with
options to enter context and control information.

## Withdrawing files

To withdraw a file, researchers select the current release request, navigate to view the file,
and use "Withdraw from Request".

!!!info "Withdrawing files"
    Files can only be withdrawn whilst a release request is in the Pending or Returned states.
    If a request is in the Submitted or Reviewed state, it should first be returned to
    the author in order to withdraw a file.
    Once a request reaches the Approved, Released, Rejected, or Withdrawn states,
    files can no longer be withdrawn through this mechanism. If it is necessary
    to withdraw a file in this case, please refer to the documentation for
    [reporting a data breach](https://docs.opensafely.org/releasing-files/#reporting-a-data-breach).


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


## Submitting a request

Once the researcher has finished working on the release request, the next step is to
submit it for a review by an output checker. Researchers should view the current request
and click "Submit For Review".

The status of the release request will transition to "Submitted".

A GitHub issue will be automatically created and output-checkers will be notified
in Slack of the new release request.

