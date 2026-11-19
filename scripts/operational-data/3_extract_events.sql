-- Output all audit events with columns:
--  - user_id
--  - request_id
--  - occurred_at
--  - user_role
-- The request author is a "researcher". Other users are classified "checker"
-- if they have submitted a review, voted on a file, downloaded a request file,
-- returned (including early return), approved or rejected the request.
-- Role is fixed per user/request across all events in a request.

WITH checkers AS (
  SELECT DISTINCT user, request FROM local_db_auditlog
  WHERE request IN (SELECT request_id FROM tmp_request_summary)
  AND type IN (
    'REQUEST_REVIEW',
    'REQUEST_FILE_APPROVE',
    'REQUEST_FILE_REQUEST_CHANGES',
    'REQUEST_FILE_DOWNLOAD',
    'REQUEST_RETURN',
    'REQUEST_EARLY_RETURN',
    'REQUEST_APPROVE',
    'REQUEST_REJECT'
  )
)
SELECT
  a.user AS user_id,
  a.request AS request_id,
  a.created_at AS occurred_at,
  CASE
    WHEN a.user = r.author THEN 'researcher'
    WHEN c.user IS NOT NULL THEN 'checker'
  END AS user_role
FROM local_db_auditlog a
LEFT JOIN local_db_requestmetadata r ON r.id = a.request
LEFT JOIN checkers c ON c.user = a.user AND c.request = a.request
WHERE a.user IN (
  SELECT DISTINCT a.user FROM local_db_auditlog a
  JOIN tmp_request_summary r ON r.request_id=a.request
  WHERE a.user IS NOT NULL AND a.user<>''
)
ORDER BY julianday(a.created_at), a.id;
