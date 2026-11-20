.bail on
.mode json
BEGIN;
-- need "requests" and "events" data for further python processing, so output
-- them to json files
.read 1_create_temp_requests_table.sql
.once requests.json
.read 2_extract_requests.sql
.once events.json
.read 3_extract_events.sql
-- "files" data not needed for further processing, so output direct to csv file
.headers on
.mode csv
.once csvs-to-export/files.csv
.read 4_extract_files.sql
.output stdout
ROLLBACK;
