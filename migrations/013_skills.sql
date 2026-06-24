-- 013_skills.sql — per-user reusable instruction/prompt presets ("skills").
--
-- A skill is a user-owned, reusable instruction preset (a persona / system
-- instruction the user can attach to a /chatagent/v3 turn). Every skill is
-- private to its owner: every read/write query filters by `user_id`, and there
-- is no cross-user access path. The owner is the resolved `user_id` from the
-- request (auth/middleware), never a value the client can set in the body.
--
-- Surrogate id PK per 00_rule.md Database Practices. `skill_id` is the
-- CHAR(26) UUIDv7→Crockford Base32 business key (the value APIs/logs reference)
-- and is UNIQUE so application code cannot create duplicates by accident.
-- `(user_id, name)` is UNIQUE so the database — not application code — refuses
-- two skills with the same name for the same owner. `(user_id, skill_id)`
-- backs the owner-scoped item lookup; `(user_id, name)` already covers the
-- owner-scoped list ORDER BY.
--
-- No physical FK on `user_id` per 00_rule.md (relationships are application-level).

CREATE TABLE IF NOT EXISTS skills (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  skill_id     CHAR(26)      NOT NULL,
  user_id      VARCHAR(64)   NOT NULL,
  name         VARCHAR(128)  NOT NULL,
  description  VARCHAR(512)  NOT NULL DEFAULT '',
  instructions TEXT          NOT NULL,
  enabled      BOOLEAN       NOT NULL DEFAULT TRUE,
  created_at   DATETIME(6)   NOT NULL,
  updated_at   DATETIME(6)   NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_skill_id (skill_id),
  UNIQUE KEY uq_user_name (user_id, name),
  KEY idx_user_skill (user_id, skill_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
