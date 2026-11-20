-- Creates a temp table (tmp_request_summary) with one row per request with the
-- following columns:
--  - request_id
--  - status
--  - num_submission_rounds
--  - num_files
--      Final number of files in a submission - will miss files removed in
--      later submission rounds - but for an approximation it's fine
--  - created_at
--  - approved_at
--  - rejected_at
--  - withdrawn_at
--
-- Limit to requests created on or after 2024-05-08 which was when job_id was
-- added to each file in a request.

CREATE TEMP TABLE tmp_request_summary AS
WITH submissions AS (
  -- Number of submission rounds per request
  SELECT request AS request_id, COUNT(*) AS num_submission_rounds
  FROM local_db_auditlog
  WHERE type = 'REQUEST_SUBMIT' AND request IS NOT NULL
  GROUP BY request
), history AS (
  -- key dates for each request
  SELECT a.request AS request_id,
    MIN(CASE WHEN a.type = 'REQUEST_APPROVE' THEN a.created_at END) AS approved_at,
    MIN(CASE WHEN a.type = 'REQUEST_REJECT' THEN a.created_at END) AS rejected_at,
    MIN(CASE WHEN a.type = 'REQUEST_WITHDRAW' THEN a.created_at END) AS withdrawn_at
  FROM local_db_auditlog a JOIN submissions s ON s.request_id = a.request
  GROUP BY a.request
)
SELECT
  s.request_id,
  s.num_submission_rounds,
  (
    SELECT COUNT(*) FROM local_db_requestfilemetadata f
    WHERE f.request_id = s.request_id AND f.filetype = 'OUTPUT'
  ) AS num_files,
  r.status,
  r.created_at,
  h.approved_at,
  h.rejected_at,
  h.withdrawn_at
FROM submissions s JOIN history h USING (request_id)
JOIN local_db_requestmetadata r ON r.id = s.request_id
WHERE julianday(r.created_at) >= julianday('2024-05-08');

