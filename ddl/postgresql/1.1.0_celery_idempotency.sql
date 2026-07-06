CREATE TABLE celery_task_record (
    id                          BIGINT PRIMARY KEY,
    task_name                   VARCHAR(255) NOT NULL,
    trace_id                    VARCHAR(128) NOT NULL,
    scope                       VARCHAR(128) NOT NULL,
    idempotency_key_hash        CHAR(64) NOT NULL,
    payload_hash                CHAR(64) NOT NULL,
    status                      VARCHAR(32) NOT NULL,
    execution_timeout_seconds   INTEGER NOT NULL,
    lease_owner                 VARCHAR(128),
    lease_expires_at            TIMESTAMPTZ,
    error_type                  VARCHAR(128),
    error_message               VARCHAR(2048),
    idempotency_expires_at      TIMESTAMPTZ NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_celery_task_record_idempotency
        UNIQUE (scope, task_name, idempotency_key_hash),
    CONSTRAINT ck_celery_task_record_status
        CHECK (status IN (
            'PENDING_PUBLISH', 'PUBLISHED', 'PUBLISH_FAILED', 'ORPHANED', 'RUNNING',
            'SUCCEEDED', 'FAILED', 'NEEDS_RECONCILIATION', 'CANCELLED'
        )),
    CONSTRAINT ck_celery_task_record_timeout CHECK (execution_timeout_seconds > 0)
);

CREATE INDEX idx_celery_task_record_trace
    ON celery_task_record (trace_id, created_at DESC, id DESC);
CREATE INDEX idx_celery_task_record_status_updated
    ON celery_task_record (status, updated_at, id);
CREATE INDEX idx_celery_task_record_expired_running
    ON celery_task_record (lease_expires_at, id) WHERE status = 'RUNNING';
CREATE INDEX idx_celery_task_record_stale_published
    ON celery_task_record (updated_at, id) WHERE status = 'PUBLISHED';
CREATE INDEX idx_celery_task_record_idempotency_expiry
    ON celery_task_record (idempotency_expires_at, id)
    WHERE status IN ('PUBLISH_FAILED', 'SUCCEEDED', 'FAILED', 'NEEDS_RECONCILIATION', 'CANCELLED');
