-- Additive catalog schema. Original inspection tables and data are preserved.
CREATE TABLE IF NOT EXISTS managed_products (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    product_id VARCHAR(128) NOT NULL,
    merchant_name VARCHAR(128) NOT NULL,
    category VARCHAR(32) NOT NULL,
    title VARCHAR(500) NOT NULL,
    description TEXT NOT NULL,
    attributes JSON NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    version INT NOT NULL DEFAULT 1,
    latest_inspection_id BIGINT NULL,
    latest_task_id VARCHAR(64) NULL,
    latest_risk VARCHAR(16) NULL,
    inspected_version INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY ix_managed_products_product_id (product_id),
    KEY ix_managed_products_merchant_name (merchant_name),
    KEY ix_managed_products_category (category),
    KEY ix_managed_products_status (status),
    KEY ix_managed_products_latest_risk (latest_risk)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS product_revisions (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    managed_product_id BIGINT NOT NULL,
    version INT NOT NULL,
    product_json JSON NOT NULL,
    actor VARCHAR(128) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_product_revision (managed_product_id, version),
    KEY ix_product_revisions_managed_product_id (managed_product_id),
    FOREIGN KEY (managed_product_id) REFERENCES managed_products (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS product_inspections (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    managed_product_id BIGINT NOT NULL,
    version INT NOT NULL,
    task_id VARCHAR(64) NULL,
    mode VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'running',
    risk_level VARCHAR(16) NULL,
    issue_count INT NOT NULL DEFAULT 0,
    degraded BOOLEAN NOT NULL DEFAULT FALSE,
    report_json JSON NULL,
    error_message TEXT NULL,
    actor VARCHAR(128) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME NULL,
    KEY ix_product_inspection_version (managed_product_id, version),
    KEY ix_product_inspections_task_id (task_id),
    FOREIGN KEY (managed_product_id) REFERENCES managed_products (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS product_audits (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    managed_product_id BIGINT NOT NULL,
    action VARCHAR(32) NOT NULL,
    from_status VARCHAR(16) NULL,
    to_status VARCHAR(16) NOT NULL,
    version INT NOT NULL,
    actor VARCHAR(128) NOT NULL,
    reason TEXT NOT NULL,
    inspection_id BIGINT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_product_audits_managed_product_id (managed_product_id),
    FOREIGN KEY (managed_product_id) REFERENCES managed_products (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
