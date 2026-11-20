-- Extracts a list of requests with their summary information.
SELECT  
  request_id,
  status,
  num_submission_rounds,
  num_files,
  created_at,
  approved_at,
  rejected_at,
  withdrawn_at
FROM tmp_request_summary
ORDER BY request_id;