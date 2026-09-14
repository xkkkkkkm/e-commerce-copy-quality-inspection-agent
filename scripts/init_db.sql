SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS product_samples (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  product_id VARCHAR(128) NOT NULL UNIQUE,
  category VARCHAR(32) NOT NULL,
  title VARCHAR(500) NOT NULL,
  description TEXT NOT NULL,
  attributes JSON NOT NULL,
  source VARCHAR(32) NOT NULL DEFAULT 'manual',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_product_samples_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS quality_rules (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  rule_id VARCHAR(128) NOT NULL UNIQUE,
  category VARCHAR(32) NOT NULL,
  issue_type VARCHAR(128) NOT NULL,
  risk_level VARCHAR(16) NOT NULL,
  rule_text TEXT NOT NULL,
  bad_examples JSON NOT NULL,
  rewrite_hint TEXT NOT NULL,
  version VARCHAR(32) NOT NULL DEFAULT '1.0',
  status VARCHAR(16) NOT NULL DEFAULT 'enabled',
  INDEX idx_quality_rules_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS inspection_tasks (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id VARCHAR(64) NOT NULL UNIQUE,
  product_id VARCHAR(128) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  trigger_source VARCHAR(32) NOT NULL DEFAULT 'api',
  input_json JSON NULL,
  input_hash VARCHAR(64) NULL,
  idempotency_key VARCHAR(128) NULL,
  error_message TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME NULL,
  INDEX idx_inspection_tasks_product_id (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS inspection_results (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id VARCHAR(64) NOT NULL UNIQUE,
  risk_level VARCHAR(16) NOT NULL,
  score INT NOT NULL,
  issues JSON NOT NULL,
  optimized_title VARCHAR(500) NOT NULL,
  optimized_description TEXT NOT NULL,
  report_json JSON NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS agent_traces (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id VARCHAR(64) NOT NULL,
  step_name VARCHAR(128) NOT NULL,
  skill_name VARCHAR(128) NULL,
  tool_name VARCHAR(128) NULL,
  input_summary TEXT NULL,
  output_summary TEXT NULL,
  latency_ms INT NOT NULL DEFAULT 0,
  status VARCHAR(16) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_agent_traces_task_id (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS evaluation_cases (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  case_id VARCHAR(128) NOT NULL UNIQUE,
  product_json JSON NOT NULL,
  expected_issues JSON NOT NULL,
  predicted_issues JSON NULL,
  metrics JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
