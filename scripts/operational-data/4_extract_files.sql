-- One row per file, with size and format, linked to request id and job id.
-- File format restricted to list here:
-- https://github.com/opensafely-core/pipeline/blob/fb6749c99361b4e64004ac369f71dfadbd50b8e9/pipeline/constants.py#L4
-- (".csv", ".jpg", ".jpeg", ".png", ".svg", ".svgz", ".html", ".txt",
--  ".log", ".json", ".md")
SELECT 
  f.request_id,
  f.job_id,
  CASE
    WHEN lower(f.relpath) LIKE '%.csv' THEN 'csv'
    WHEN lower(f.relpath) LIKE '%.jpg' OR lower(f.relpath) LIKE '%.jpeg' THEN 'jpeg'
    WHEN lower(f.relpath) LIKE '%.png' THEN 'png'
    WHEN lower(f.relpath) LIKE '%.svg' OR lower(f.relpath) LIKE '%.svgz' THEN 'svg'
    WHEN lower(f.relpath) LIKE '%.html' THEN 'html'
    WHEN lower(f.relpath) LIKE '%.txt' THEN 'text'
    WHEN lower(f.relpath) LIKE '%.log' THEN 'log'
    WHEN lower(f.relpath) LIKE '%.json' THEN 'json'
    WHEN lower(f.relpath) LIKE '%.md' THEN 'markdown'
    ELSE 'other_or_unknown'
  END AS format,
  f.size AS size_bytes
FROM local_db_requestfilemetadata f
JOIN tmp_request_summary r ON r.request_id=f.request_id
WHERE f.filetype='OUTPUT'
ORDER BY f.request_id, f.id;
