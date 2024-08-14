## What is OpenSAFELY Airlock?

Airlock is a service running inside the secure environment to allow researchers to
view output files, create release requests, and for output checkers to review and
release these requests.

Researchers use the Airlock UI to view their workspace files, create a new release request, add their files to the release request, provide context information and
description of applied statistical disclosure controls, and submit the files for review.

Output checkers review the files requested for release, and approve or request changes for
each one. Once a full review of all files has been completed by two output checkers and all
filees are approved, an output checker can release the files to the Jobs site.

Airlock allows us to enforce some safety controls and policies automatically. This includes things such as the number of required reviews, independence of reviews, and ensuring that researchers who are also output checkers are not able to review their own requests.


## Accessing Airlock

!!!warning "Airlock is only supported in Chrome :fontawesome-brands-chrome:"
    Please ensure you use Chrome when accessing Airlock. Features
    may not work as expected in other browsers.

To access the Airlock system:

1. Outside of Level 4 [Obtain a Single Use Token via the
    Jobs website](https://docs.opensafely.org/jobs-site/#viewing-analysis-outputs-on-the-server). 
1. Log into Level 4 and navigate to Airlock in Chrome. Airlock is
    accessed at `https://<backend>.backends.opensafely.org`. e.g. on
    the TPP backend, go to `https://tpp.backends.opensafely.org`.
1. Log in using your GitHub username or email and the single use token. 

(Note: Airlock is not supported on browsers other than Chrome.)

For more details, see the section on [how to access Airlock](how-tos/access-airlock.md)


## Workflow and permission

As a release request progresses through the construction, submission and review stages, the
actions that you are allowed to perform on a request or on files within a request changes.

For example, a researcher creates and constructs a release request, and then submits it
for review. After submission, the researcher can no longer edit the request. Output checkers
now have access to the submitted request and can review and approve or request changes to
files. They may then return the request to the researcher with comments.  At this stage the
request can now be edited by the researcher, and the output checkers no longer have
permission to change their decisions on files or to add comments.

Refer to the [reference documentation on Airlock workflow and permissions](reference/workflow-and-permissions) for a more detailed description.


## Using the documentation

Airlock's documentation has three main sections:

The [how-to guides](how-to/index.md) provide practical steps for working with Airlock to create and process release requests. These are additionally subdivided into guides for researchers and guides for output checkers.

The [explanation](explanation/index.md) section provides background information about Airlock and its features.

The [reference](reference/index.md) section provides background information about working with Airlock.


---8<-- 'reference/glossary.md'
