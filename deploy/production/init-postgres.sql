-- The official image runs this once, only for an empty data volume.
-- psql quotes values as SQL literals, including passwords with punctuation.
\getenv app_password APP_DATABASE_PASSWORD
\getenv runner_password RUNNER_DATABASE_PASSWORD
CREATE ROLE secondshift LOGIN PASSWORD :'app_password';
CREATE ROLE secondshift_runner LOGIN PASSWORD :'runner_password';
CREATE DATABASE secondshift OWNER secondshift;
CREATE DATABASE secondshift_runner OWNER secondshift_runner;
REVOKE ALL ON DATABASE secondshift FROM PUBLIC;
REVOKE ALL ON DATABASE secondshift_runner FROM PUBLIC;
