import duckdb

con = duckdb.connect("gcat.duckdb")

# ==============================================
#Query 1: Top providers by total launches, 2015+
# ==============================================

# print(con.execute("""
#     SELECT
#         l.agency_code,
#         o.Name AS provider,
#         COUNT(*) AS launches,
#         SUM(CASE WHEN l.success THEN 1 ELSE 0 END) AS successes
#     FROM launches l
#     LEFT JOIN raw_orgs o ON l.agency_code = o.Code
#     WHERE l.launch_year >= 2015
#     GROUP BY l.agency_code, o.Name
#     ORDER BY launches DESC
#     LIMIT 15
# """).df().to_string(index=False))

# ==============================================
#Query 2: Provider market share by year
# ==============================================

con.execute("""
CREATE OR REPLACE VIEW launches_named AS
SELECT *,
    CASE
        WHEN agency_code = 'SPX' THEN 'SpaceX'
        WHEN agency_code IN ('CALT','SAST','EXPACE','XIDO','ZKYT','CGWIC') THEN 'China (state & commercial)'
        WHEN agency_code IN ('FKA','KHRU','VVKO','TSSKB','RVSN') THEN 'Russia'
        WHEN agency_code IN ('ULAL','ULAB','ULA') THEN 'ULA'  
        WHEN agency_code = 'AE' THEN 'Arianespace'
        WHEN agency_code = 'RLABN' THEN 'Rocket Lab'
        WHEN agency_code = 'ISRO' THEN 'ISRO (India)'  
        WHEN agency_code = 'MHI' THEN 'MHI (Japan)'
        ELSE 'Other'
    END AS provider
FROM launches    
""")    

# Market share by provider by year

print(con.execute("""
    SELECT provider,
        COUNT(*) FILTER (launch_year = 2015) AS y2015,
        COUNT(*) FILTER (launch_year = 2017) AS y2017,
        COUNT(*) FILTER (launch_year = 2019) AS y2019,
        COUNT(*) FILTER (launch_year = 2021) AS y2021,
        COUNT(*) FILTER (launch_year = 2023) AS y2023,
        COUNT(*) FILTER (launch_year = 2025) AS y2025
    FROM launches_named
    WHERE launch_year BETWEEN 2015 AND 2025
    GROUP BY provider
    ORDER BY y2025 DESC
""").df().to_string(index=False))