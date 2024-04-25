!!!warning "Work on Airlock is still in progress"
    Airlock is a new tool for managing the output release request
    and review process. While Airlock is in initial development stages,
    a combination of the current and new (Airlock) process
    will be maintained.


## What is OpenSAFELY Airlock?

Airlock is a service running inside the secure environment to allow researchers to
view output files, create release requests, and for output checkers to review and
release these requests.

Researchers use the Airlock UI to view their workspace files, create a new release request, add their files to the release and submit them for review. Researchers can only have at most one open release request per workspace, but files can be organised by the researchers into logical groups when constructing the request.

Output checkers are able to review submitted release requests, including viewing the files within the request, approving or rejecting these files. They can then approve or reject the request as a whole, and finally release the files to the job-server.

Airlock allows us to enforce some safety controls and policies automatically. This includes things such as the number of required reviews, and ensuring that researchers who are also output checkers are not able to review their own requests.


## Accessing Airlock


!!!warning "Airlock is only supported in Chrome :fontawesome-brands-chrome:"
    Please ensure you use Chrome when accessing Airlock. Features
    may not work as expected in other browsers.

To access the Airlock system:

1. [Obtain a Single Use Token via the
    Jobs website](https://docs.opensafely.org/jobs-site/#viewing-analysis-outputs-on-the-server). 
1. Log into Level 4 and navigate to Airlock in Chrome. Airlock is
    accessed at `https://<backend>.backends.opensafely.org`. e.g. on
    the TPP backend, go to `https://tpp.backends.opensafely.org`.
1. Log in using your GitHub username or email and the single use token. 

(Note: Airlock is not supported on browsers other than Chrome.)


## Viewing files

Users can view medium privacy outputs from any workspace they have permission to
access (workspaces for which they have the Project Developer role) via Airlock if: 

* They have access to log into the backend (they must be accessing Airlock
    from a browser in the chosen backend)
* The backend has Airlock installed and running.

