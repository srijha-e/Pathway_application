-- Migration 002: add 'awaiting_credentials' to credit_reports.status CHECK
--
-- Run this against any DB that already had the initial schema.sql applied.
-- Safe to re-run (drops then re-adds the constraint).

ALTER TABLE credit_reports
  DROP CONSTRAINT IF EXISTS credit_reports_status_check;

ALTER TABLE credit_reports
  ADD CONSTRAINT credit_reports_status_check
    CHECK (status IN ('pending','running','awaiting_input','awaiting_credentials','done','failed'));
