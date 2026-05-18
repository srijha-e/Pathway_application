-- Migration 003: track per-question retry attempts on security Qs.
--
-- 1 = first time we showed this question.
-- N>1 = the user has answered wrong (N-1) times; IIQ is showing the same Q again.
-- Scraper fails the report when attempt_count exceeds MAX_QUESTION_ATTEMPTS (3).

ALTER TABLE credit_report_questions
  ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 1;
