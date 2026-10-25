-- Hand-written fallback; the app prefers sql/repo_health.sql (written with CoCo).
-- GH = database name chosen when getting "Snowflake Public Data (Free)" from the Marketplace.
WITH rid AS (
    SELECT REPO_ID FROM GH.PUBLIC_DATA.GITHUB_REPOS
    WHERE REPO_NAME = $REPO_NAME
    QUALIFY ROW_NUMBER() OVER (ORDER BY LAST_SEEN DESC) = 1
),
ev AS (
    SELECT TYPE, ACTOR_ID, CREATED_AT_TIMESTAMP FROM GH.PUBLIC_DATA.GITHUB_EVENTS
    WHERE REPO_ID = (SELECT REPO_ID FROM rid)
      AND CREATED_AT_TIMESTAMP >= DATEADD(day, -30, CURRENT_TIMESTAMP())
)
SELECT
    (SELECT SUM(COUNT) FROM GH.PUBLIC_DATA.GITHUB_STARS
      WHERE REPO_ID = (SELECT REPO_ID FROM rid) AND DATE >= DATEADD(day, -90, CURRENT_DATE)) AS STARS_LAST_90D,
    (SELECT COUNT(DISTINCT ACTOR_ID) FROM ev)                                 AS ACTIVE_CONTRIBUTORS_30D,
    (SELECT COUNT(*) FROM ev WHERE TYPE IN ('IssuesEvent', 'IssueCommentEvent')) AS ISSUE_EVENTS_30D,
    (SELECT COUNT(*) FROM ev WHERE TYPE LIKE 'PullRequest%')                  AS PR_EVENTS_30D,
    (SELECT MAX(CREATED_AT_TIMESTAMP)::DATE FROM ev)                          AS LAST_ACTIVITY_DATE
