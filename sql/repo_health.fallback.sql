-- Hand-written fallback; the app prefers sql/repo_health.sql (written with CoCo).
-- SNOWFLAKE_CURATED_DATA_FREE = database created by getting "Snowflake Public Data (Free)" from the Marketplace.
-- The free tier lags (events ~3 months; stars and repos ~1 day), so the event window is wide.
WITH rid AS (
    SELECT REPO_ID FROM SNOWFLAKE_CURATED_DATA_FREE.PUBLIC_DATA_FREE.GITHUB_REPOS
    WHERE REPO_NAME = $REPO_NAME
    QUALIFY ROW_NUMBER() OVER (ORDER BY LAST_SEEN DESC) = 1
),
ev AS (
    SELECT TYPE, ACTOR_ID, CREATED_AT_TIMESTAMP FROM SNOWFLAKE_CURATED_DATA_FREE.PUBLIC_DATA_FREE.GITHUB_EVENTS
    WHERE REPO_ID = (SELECT REPO_ID FROM rid)
      AND CREATED_AT_TIMESTAMP >= DATEADD(day, -180, CURRENT_TIMESTAMP())
),
st AS (
    SELECT DATE, COUNT FROM SNOWFLAKE_CURATED_DATA_FREE.PUBLIC_DATA_FREE.GITHUB_STARS
    WHERE REPO_ID = (SELECT REPO_ID FROM rid)
)
SELECT
    (SELECT SUM(COUNT) FROM st)                                                   AS STARS_ALL_TIME,
    (SELECT SUM(COUNT) FROM st WHERE DATE >= DATEADD(day, -365, CURRENT_DATE))    AS STARS_LAST_365D,
    (SELECT COUNT(DISTINCT ACTOR_ID) FROM ev)                                     AS ACTIVE_CONTRIBUTORS_180D,
    (SELECT COUNT(*) FROM ev WHERE TYPE IN ('IssuesEvent', 'IssueCommentEvent'))  AS ISSUE_EVENTS_180D,
    (SELECT COUNT(*) FROM ev WHERE TYPE LIKE 'PullRequest%')                      AS PR_EVENTS_180D,
    (SELECT MAX(CREATED_AT_TIMESTAMP)::DATE FROM ev)                              AS LAST_ACTIVITY_DATE
