MSQL = {
 "M01": """
SELECT sector_name, SUM(ead_reported) AS ead_reported_sar_mn,
       COUNT(DISTINCT facility_id) AS facilities
FROM cockpit_facility_quarter
WHERE reporting_quarter = $q AND sector_name IS NOT NULL
GROUP BY 1 ORDER BY ead_reported_sar_mn DESC, sector_name ASC
""",
 "M02": """
SELECT sector_name, SUM(ecl_reported) AS ecl_reported_sar_mn
FROM cockpit_facility_quarter
WHERE reporting_quarter = $q AND sector_name IS NOT NULL
GROUP BY 1 ORDER BY ecl_reported_sar_mn DESC, sector_name ASC
""",
 "M03": """
SELECT sector_name, SUM(ead_reported) AS stage2_ead_sar_mn
FROM cockpit_facility_quarter
WHERE reporting_quarter = $q AND ifrs9_stage = 2 AND sector_name IS NOT NULL
GROUP BY 1 HAVING SUM(ead_reported) > 0
ORDER BY stage2_ead_sar_mn DESC, sector_name ASC
""",
 "M04": """
SELECT borrower_id, ANY_VALUE(sector_name) AS sector_name,
       SUM(ead_reported) AS ead_reported_sar_mn,
       SUM(ecl_reported) AS ecl_reported_sar_mn,
       MAX(ifrs9_stage) AS stage
FROM cockpit_facility_quarter
WHERE reporting_quarter = $q AND sector_name IS NOT NULL
GROUP BY 1 ORDER BY ecl_reported_sar_mn DESC, borrower_id ASC LIMIT 10
""",
 "M05": """
SELECT sector_name, SUM(ead_reported) AS ead_reported_sar_mn
FROM cockpit_facility_quarter
WHERE reporting_quarter = $q AND sector_name IS NOT NULL
GROUP BY 1 ORDER BY ead_reported_sar_mn DESC, sector_name ASC
""",
 "M06": """
WITH s AS (
  SELECT reporting_quarter, sector_name,
         SUM(CASE WHEN ifrs9_stage = 2 THEN ead_reported ELSE 0 END) AS s2
  FROM cockpit_facility_quarter
  WHERE reporting_quarter IN ($q, $y) AND sector_name IS NOT NULL
  GROUP BY 1, 2)
SELECT COALESCE(n.sector_name, o.sector_name) AS sector_name,
       COALESCE(n.s2, 0) AS current_stage2_sar_mn,
       COALESCE(o.s2, 0) AS prior_stage2_sar_mn,
       COALESCE(n.s2, 0) - COALESCE(o.s2, 0) AS change_sar_mn
FROM (SELECT * FROM s WHERE reporting_quarter = $q) n
FULL OUTER JOIN (SELECT * FROM s WHERE reporting_quarter = $y) o
  ON o.sector_name = n.sector_name
ORDER BY change_sar_mn DESC, sector_name ASC
""",
 "M07": """
WITH s AS (
  SELECT reporting_quarter, sector_name,
         SUM(ead_reported) AS ead, SUM(ecl_reported) AS ecl
  FROM cockpit_facility_quarter
  WHERE reporting_quarter IN ($q, $p) AND sector_name IS NOT NULL
  GROUP BY 1, 2)
SELECT n.sector_name,
       (n.ecl - o.ecl) / o.ecl AS ecl_growth,
       (n.ead - o.ead) / o.ead AS ead_growth,
       (n.ecl - o.ecl) / o.ecl - (n.ead - o.ead) / o.ead AS gap
FROM s n JOIN s o ON o.sector_name = n.sector_name
  AND o.reporting_quarter = $p
WHERE n.reporting_quarter = $q AND o.ecl <> 0 AND o.ead <> 0
  AND (n.ecl - o.ecl) / o.ecl > (n.ead - o.ead) / o.ead
ORDER BY gap DESC, n.sector_name ASC
""",
 "M08": """
WITH s AS (
  SELECT reporting_quarter, sector_name, SUM(ecl_reported) AS ecl
  FROM cockpit_facility_quarter
  WHERE reporting_quarter IN ($q, $p) AND sector_name IS NOT NULL
  GROUP BY 1, 2)
SELECT COALESCE(n.sector_name, o.sector_name) AS sector_name,
       COALESCE(n.ecl, 0) - COALESCE(o.ecl, 0) AS contribution_sar_mn
FROM (SELECT * FROM s WHERE reporting_quarter = $q) n
FULL OUTER JOIN (SELECT * FROM s WHERE reporting_quarter = $p) o
  ON o.sector_name = n.sector_name
ORDER BY ABS(COALESCE(n.ecl, 0) - COALESCE(o.ecl, 0)) DESC, sector_name ASC
""",
 "M09": """
WITH b AS (
  SELECT reporting_quarter, borrower_id, SUM(ecl_reported) AS ecl
  FROM cockpit_facility_quarter
  WHERE reporting_quarter IN ($q, $p) AND sector_name IS NOT NULL
  GROUP BY 1, 2)
SELECT COALESCE(n.borrower_id, o.borrower_id) AS borrower_id,
       COALESCE(n.ecl, 0) - COALESCE(o.ecl, 0) AS increase_sar_mn,
       COALESCE(n.ecl, 0) AS ecl_reported_sar_mn
FROM (SELECT * FROM b WHERE reporting_quarter = $q) n
FULL OUTER JOIN (SELECT * FROM b WHERE reporting_quarter = $p) o
  ON o.borrower_id = n.borrower_id
WHERE COALESCE(n.ecl, 0) - COALESCE(o.ecl, 0) > 0
ORDER BY increase_sar_mn DESC, borrower_id ASC
""",
 "M10": """
WITH b AS (
  SELECT reporting_quarter, borrower_id, SUM(ead_reported) AS ead,
         SUM(ecl_reported) AS ecl, MAX(ifrs9_stage) AS stage
  FROM cockpit_facility_quarter
  WHERE reporting_quarter IN ($q, $p) AND sector_name IS NOT NULL
  GROUP BY 1, 2),
r AS (SELECT reporting_quarter, borrower_id, rating_rank
      FROM cockpit_rating_ratio_quarter
      WHERE reporting_quarter IN ($q, $p))
SELECT nb.borrower_id, nb.ead AS ead_reported_sar_mn,
       nb.ecl AS ecl_reported_sar_mn
FROM (SELECT * FROM b WHERE reporting_quarter = $q) nb
JOIN (SELECT * FROM b WHERE reporting_quarter = $p) ob
  ON ob.borrower_id = nb.borrower_id
JOIN (SELECT * FROM r WHERE reporting_quarter = $q) nr
  ON nr.borrower_id = nb.borrower_id
JOIN (SELECT * FROM r WHERE reporting_quarter = $p) orr
  ON orr.borrower_id = nb.borrower_id
WHERE nr.rating_rank > orr.rating_rank AND nb.stage >= 2 AND ob.stage < 2
ORDER BY nb.borrower_id ASC
""",
 "M11": """
SELECT reporting_quarter,
       SUM(ead_reported) AS ead_reported_sar_mn,
       SUM(ecl_reported) AS ecl_reported_sar_mn,
       SUM(CASE WHEN ifrs9_stage = 2 THEN ead_reported ELSE 0 END)
           AS stage2_ead_sar_mn
FROM cockpit_facility_quarter
WHERE reporting_quarter IN ($q, $p) AND sector_name IS NOT NULL
GROUP BY 1 ORDER BY reporting_quarter DESC
""",
 "M12": """
SELECT reporting_quarter,
       SUM(ead_reported) AS ead_reported_sar_mn,
       SUM(ecl_reported) AS ecl_reported_sar_mn,
       SUM(CASE WHEN ifrs9_stage = 2 THEN ead_reported ELSE 0 END)
           AS stage2_ead_sar_mn
FROM cockpit_facility_quarter
WHERE reporting_quarter IN ($q, $y) AND sector_name = $sector
GROUP BY 1 ORDER BY reporting_quarter DESC
""",
 "M13": """
WITH s AS (
  SELECT reporting_quarter, sector_name, SUM(ecl_reported) AS ecl,
         SUM(CASE WHEN collateral_coverage_ratio IS NOT NULL
                    AND collateral_coverage_ratio < 1
                  THEN ead_reported ELSE 0 END) / SUM(ead_reported)
             AS uncovered
  FROM cockpit_facility_quarter
  WHERE reporting_quarter IN ($q, $p) AND sector_name IS NOT NULL
  GROUP BY 1, 2)
SELECT n.sector_name,
       n.ecl - o.ecl AS ecl_change_sar_mn,
       n.uncovered - o.uncovered AS uncovered_share_change
FROM s n JOIN s o ON o.sector_name = n.sector_name
  AND o.reporting_quarter = $p
WHERE n.reporting_quarter = $q AND n.ecl > o.ecl
  AND n.uncovered > o.uncovered
ORDER BY ecl_change_sar_mn DESC, n.sector_name ASC
""",
 "M14": """
SELECT borrower_id, SUM(ecl_reported) AS ecl_reported_sar_mn
FROM cockpit_facility_quarter
WHERE reporting_quarter = $q AND sector_name IS NOT NULL
GROUP BY 1 ORDER BY ecl_reported_sar_mn DESC, borrower_id ASC LIMIT 10
""",
 "M15": """
WITH b AS (
  SELECT sector_name, borrower_id, SUM(ecl_reported) AS ecl
  FROM cockpit_facility_quarter
  WHERE reporting_quarter = $q AND sector_name IS NOT NULL
  GROUP BY 1, 2),
ranked AS (SELECT *, ROW_NUMBER() OVER (
             PARTITION BY sector_name ORDER BY ecl DESC, borrower_id ASC) AS rn
           FROM b)
SELECT sector_name,
       SUM(CASE WHEN rn <= 3 THEN ecl ELSE 0 END) AS top3_ecl_sar_mn,
       SUM(ecl) AS sector_ecl_sar_mn,
       SUM(CASE WHEN rn <= 3 THEN ecl ELSE 0 END) / SUM(ecl) AS share
FROM ranked GROUP BY 1 HAVING SUM(ecl) > 0
ORDER BY share DESC, sector_name ASC
""",
}

PARAMS = {
 "M01": ("q",), "M02": ("q",), "M03": ("q",), "M04": ("q",), "M05": ("q",),
 "M06": ("q", "y"), "M07": ("q", "p"), "M08": ("q", "p"), "M09": ("q", "p"),
 "M10": ("q", "p"), "M11": ("q", "p"), "M12": ("q", "y", "sector"),
 "M13": ("q", "p"), "M14": ("q",), "M15": ("q",),
}
