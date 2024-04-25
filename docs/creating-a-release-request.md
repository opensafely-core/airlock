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
    Files can only be withdrawn whilst a release request is in the Pending or Submitted states.
    Once a request reaches the Approved, Released, Rejected, or Withdrawn states,
    files can no longer be withdrawn through this mechanism. If it is necessary
    to withdraw a file in this case, please refer to the documentation for
    [reporting a data breach](https://docs.opensafely.org/releasing-files/#reporting-a-data-breach).


## Updating files

When a file is added to a release request, a copy is taken of the current state
of that file. This is deliberate, and ensures that a file added to a request
does not change.

If a subsequent a job is run within that workspace which changes the file,
the workspace view will show the new file contents and the release request view
will continue to show the old file contents from the time that it was added to
the request.

In order to update a file, the researcher must first follow the steps above to
withdraw the file from the request. They will then be able to add the updated file.

!!!note ""
    :construction: There are more features coming soon involving updating files.
