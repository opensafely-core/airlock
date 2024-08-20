## Workspace

On the Jobs site, a workspace is linked to a GitHub repository in the OpenSAFELY organisation. Actions are run within the workspace, and generate outputs as defined in the repo's `project.yaml`.

On Airlock, a Workspace represents the Jobs site workspace as a directory of files
generated from the jobs that have been run.


## Release request

A term used to refer to a request to release one or more output files from a workspace to the Jobs site.

Sometimes referred to as just "request".

A request is also represented in Airlock as a directory of file groups and the files they contain.


## Output File

A file that has been added to a release request and is to be released.

## Supporting File

A file that has been added to a release request for additional information or context, and will
not be released.

## File Group

A collection of files that share the same context and disclosure control information.

## Context

Contextual description of the output files within a file group explaining e.g.:

- why these files are requested for release
- variable descriptions
- description and count of the underlying sample of the population
- population size and degrees of freedom for regression outputs
- relationship to other data/tables which through combination may introduce secondary disclosive risks.


## Controls

Description of statistical disclosure control measures that have been applied to the files within a file group. 

## Turn

A stage of the release request process during which the request is considered to be "owned"
by either the researcher (author) or the reviewer (output checker). 

## Independent Review

Each time a release request is submitted for review, it is initially reviewed independently by two output checkers. At this stage, output checkers are not aware of the status of other 
reviews, and cannot see comments made by other output checker.

## Consolidation

After output checkers have both completed their independent review, there is a phase of 
consolidation, where they can collaborate and determine the questions and feedback that
may be required from the researcher, prior to deciding whether this request is now ready
for release.
