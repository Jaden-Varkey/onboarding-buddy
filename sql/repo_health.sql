-- Repo health for $REPO_NAME (one row). Written with CoCo.
-- MAX_BY gives a plain scalar REPO_ID so the events/stars scans prune on it;
-- each view is then read once with conditional aggregates.
WITH rid AS (
    SELECT MAX_BY(REPO_ID, LAST_SEEN) AS REPO_ID
    FROM SNOWFLAKE_CURATED_DATA_FREE.PUBLIC_DATA_FREE.GITHUB_REPOS
    WHERE REPO_NAME = $REPO_NAME
),
ev AS (
    SELECT
        COUNT(DISTINCT ACTOR_ID)                                  AS ACTIVE_CONTRIBUTORS_180D,
        COUNT_IF(TYPE IN ('IssuesEvent', 'IssueCommentEvent'))    AS ISSUE_EVENTS_180D,
        COUNT_IF(TYPE LIKE 'PullRequest%')                        AS PR_EVENTS_180D,
        MAX(CREATED_AT_TIMESTAMP)::DATE                           AS LAST_ACTIVITY_DATE
    FROM SNOWFLAKE_CURATED_DATA_FREE.PUBLIC_DATA_FREE.GITHUB_EVENTS
    WHERE REPO_ID = (SELECT REPO_ID FROM rid)
      AND CREATED_AT_TIMESTAMP >= DATEADD(day, -180, CURRENT_TIMESTAMP())
),
st AS (
    SELECT
        SUM(COUNT)                                                       AS STARS_ALL_TIME,
        SUM(IFF(DATE >= DATEADD(day, -365, CURRENT_DATE), COUNT, NULL))  AS STARS_LAST_365D
    FROM SNOWFLAKE_CURATED_DATA_FREE.PUBLIC_DATA_FREE.GITHUB_STARS
    WHERE REPO_ID = (SELECT REPO_ID FROM rid)
)
SELECT
    st.STARS_ALL_TIME,
    st.STARS_LAST_365D,
    ev.ACTIVE_CONTRIBUTORS_180D,
    ev.ISSUE_EVENTS_180D,
    ev.PR_EVENTS_180D,
    ev.LAST_ACTIVITY_DATE
FROM st CROSS JOIN ev
