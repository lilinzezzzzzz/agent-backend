CREATE TABLE celery_task_record (
    id                      BIGINT PRIMARY KEY,
    task_name               VARCHAR(255) NOT NULL,
    trace_id                VARCHAR(128) NOT NULL,
    scope                   VARCHAR(128) NOT NULL,
    queue                   VARCHAR(64) NOT NULL,
    idempotency_key_hash    VARCHAR(64) NOT NULL,
    payload_hash            VARCHAR(64) NOT NULL,
    status                  VARCHAR(32) NOT NULL,
    cancel_allowed          BOOLEAN NOT NULL DEFAULT TRUE,
    execution_token         VARCHAR(64),
    attempt_count           INTEGER NOT NULL DEFAULT 0,
    queued_deadline_at      TIMESTAMP WITHOUT TIME ZONE,
    hard_deadline_at        TIMESTAMP WITHOUT TIME ZONE,
    fence_expires_at        TIMESTAMP WITHOUT TIME ZONE,
    started_at              TIMESTAMP WITHOUT TIME ZONE,
    finished_at             TIMESTAMP WITHOUT TIME ZONE,
    error_code              VARCHAR(64),
    error_summary           VARCHAR(512),
    creator_id              BIGINT,
    creator_type            VARCHAR(32) NOT NULL,
    updater_id              BIGINT,
    updater_type            VARCHAR(32),
    created_at              TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at              TIMESTAMP WITHOUT TIME ZONE,
    deleted_at              TIMESTAMP WITHOUT TIME ZONE,
    CONSTRAINT uq_celery_task_record_idempotency
        UNIQUE (scope, task_name, idempotency_key_hash),
    CONSTRAINT ck_celery_task_record_status
        CHECK (status IN (
            'SUBMITTING', 'QUEUED', 'RUNNING', 'CANCELLING',
            'ORPHANED', 'SUCCEEDED', 'FAILED', 'CANCELLED'
        )),
    CONSTRAINT ck_celery_task_record_creator_type
        CHECK (creator_type IN ('user', 'system', 'service', 'task')),
    CONSTRAINT ck_celery_task_record_updater_type
        CHECK (
            updater_type IS NULL
            OR updater_type IN ('user', 'system', 'service', 'task')
        ),
    CONSTRAINT ck_celery_task_record_attempt_count CHECK (attempt_count >= 0)
);

CREATE INDEX idx_celery_task_record_scope_trace
    ON celery_task_record (scope, trace_id, created_at DESC, id DESC);
CREATE INDEX idx_celery_task_record_scope_status
    ON celery_task_record (scope, status, created_at, id);
CREATE INDEX idx_celery_task_record_stale_submitting
    ON celery_task_record (updated_at, id) WHERE status = 'SUBMITTING';
CREATE INDEX idx_celery_task_record_queued_deadline
    ON celery_task_record (queued_deadline_at, id) WHERE status = 'QUEUED';
CREATE INDEX idx_celery_task_record_execution_deadline
    ON celery_task_record (hard_deadline_at, id)
    WHERE status IN ('RUNNING', 'CANCELLING');
CREATE INDEX idx_celery_task_record_orphan_fence
    ON celery_task_record (fence_expires_at, id)
    WHERE status = 'ORPHANED';

COMMENT ON TABLE celery_task_record IS '通用 Celery 逻辑任务事实记录';
