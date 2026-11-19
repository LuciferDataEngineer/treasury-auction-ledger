SELECT load_sequence,captured_at,window_start,window_end,raw_rows,inserted,revised,removed,unchanged,
       capture_id,raw_sha256
FROM captures ORDER BY load_sequence;
