SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS admin_sessions (
  token_hash VARCHAR(64) NOT NULL PRIMARY KEY,
  username VARCHAR(128) NOT NULL,
  expires_at DATETIME NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX ix_admin_sessions_expires_at (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
