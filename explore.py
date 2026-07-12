import duckdb

con = duckdb.connect("gcat.duckdb")

print(con.execute("""
    SELECT
        l.agency_code,
        o.Name AS provider,
        COUNT(*) AS launches,
        SUM(CASE WHEN l.success THEN 1 ELSE 0 END) AS successes
    FROM launches l
    LEFT JOIN raw_orgs o ON l.agency_code = o.Code
    WHERE l.launch_year >= 2015
    GROUP BY l.agency_code, o.Name
    ORDER BY launches DESC
    LIMIT 15
""").df().to_string(index=False))