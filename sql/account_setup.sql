-- One-time account setup (run in a Snowsight worksheet as ACCOUNTADMIN).
USE ROLE ACCOUNTADMIN;
CREATE WAREHOUSE IF NOT EXISTS ONBOARDING_WH WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE;
CREATE DATABASE IF NOT EXISTS ONBOARDING_BUDDY;
CREATE SCHEMA IF NOT EXISTS ONBOARDING_BUDDY.APP;
ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'ANY_REGION';

-- Key-pair service user for the app. Generate keys with:
--   openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out ob_app_key.p8 -nocrypt
--   openssl rsa -in ob_app_key.p8 -pubout -out ob_app_key.pub
CREATE USER IF NOT EXISTS OB_APP
  TYPE = SERVICE
  DEFAULT_ROLE = ACCOUNTADMIN
  DEFAULT_WAREHOUSE = ONBOARDING_WH
  RSA_PUBLIC_KEY = '<contents of ob_app_key.pub without the BEGIN/END lines>';
GRANT ROLE ACCOUNTADMIN TO USER OB_APP;  -- hackathon shortcut; use a narrower role in production
