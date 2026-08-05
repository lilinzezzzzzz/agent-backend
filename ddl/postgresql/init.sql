-- PostgreSQL initial schema generated from internal/models.
-- This file targets a fresh scaffold database; it is not a migration script.

CREATE TABLE agent_audit (
    run_id VARCHAR(64) NOT NULL,
    agent_name VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL,
    trace_id VARCHAR(128),
    user_input TEXT NOT NULL,
    max_steps INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL,
    final_answer TEXT,
    started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    ended_at TIMESTAMP WITHOUT TIME ZONE,
    elapsed_ms FLOAT NOT NULL,
    steps JSONB NOT NULL,
    llm_calls JSONB NOT NULL,
    metadata JSONB,
    id BIGINT NOT NULL,
    creator_id BIGINT,
    creator_type VARCHAR(32) NOT NULL,
    updater_id BIGINT,
    updater_type VARCHAR(32),
    updated_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    deleted_at TIMESTAMP WITHOUT TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_audit_agent_run UNIQUE (agent_name, run_id)
);

CREATE INDEX idx_agent_audit_agent_status
    ON agent_audit (agent_name, status);
CREATE INDEX idx_agent_audit_trace_id
    ON agent_audit (trace_id);
CREATE INDEX idx_agent_audit_user_created
    ON agent_audit (user_id, created_at);

CREATE TABLE agent_message (
    message_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64),
    user_id BIGINT NOT NULL,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    content_summary TEXT,
    token_count INTEGER NOT NULL,
    metadata JSONB,
    id BIGINT NOT NULL,
    creator_id BIGINT,
    creator_type VARCHAR(32) NOT NULL,
    updater_id BIGINT,
    updater_type VARCHAR(32),
    updated_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    deleted_at TIMESTAMP WITHOUT TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_message_message_id UNIQUE (message_id)
);

CREATE INDEX idx_agent_message_run_id
    ON agent_message (run_id);
CREATE INDEX idx_agent_message_session_created
    ON agent_message (session_id, created_at, id);
CREATE INDEX idx_agent_message_user_created
    ON agent_message (user_id, created_at, id);

CREATE TABLE agent_run (
    run_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL,
    entrypoint VARCHAR(32) NOT NULL,
    agent_name VARCHAR(64) NOT NULL,
    route VARCHAR(32),
    status VARCHAR(32) NOT NULL,
    max_steps INTEGER NOT NULL,
    trace_id VARCHAR(128),
    started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    ended_at TIMESTAMP WITHOUT TIME ZONE,
    elapsed_ms FLOAT NOT NULL,
    error_code VARCHAR(64),
    error_message TEXT,
    metadata JSONB,
    id BIGINT NOT NULL,
    creator_id BIGINT,
    creator_type VARCHAR(32) NOT NULL,
    updater_id BIGINT,
    updater_type VARCHAR(32),
    updated_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    deleted_at TIMESTAMP WITHOUT TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_run_run_id UNIQUE (run_id)
);

CREATE INDEX idx_agent_run_agent_status
    ON agent_run (agent_name, status);
CREATE INDEX idx_agent_run_session_created
    ON agent_run (session_id, created_at, id);
CREATE INDEX idx_agent_run_trace_id
    ON agent_run (trace_id);
CREATE INDEX idx_agent_run_user_created
    ON agent_run (user_id, created_at, id);

CREATE TABLE agent_run_step (
    run_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL,
    step_index INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL,
    action_type VARCHAR(32) NOT NULL,
    tool VARCHAR(64),
    args JSONB,
    action_result JSONB,
    artifact_id VARCHAR(64),
    error TEXT,
    elapsed_ms FLOAT NOT NULL,
    id BIGINT NOT NULL,
    creator_id BIGINT,
    creator_type VARCHAR(32) NOT NULL,
    updater_id BIGINT,
    updater_type VARCHAR(32),
    updated_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    deleted_at TIMESTAMP WITHOUT TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_run_step_run_index UNIQUE (run_id, step_index)
);

CREATE INDEX idx_agent_run_step_session_created
    ON agent_run_step (session_id, created_at, id);
CREATE INDEX idx_agent_run_step_tool
    ON agent_run_step (tool);
CREATE INDEX idx_agent_run_step_user_created
    ON agent_run_step (user_id, created_at, id);

CREATE TABLE agent_session (
    session_id VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL,
    entrypoint VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    title VARCHAR(128),
    rolling_summary TEXT,
    working_state JSONB,
    recent_token_count INTEGER NOT NULL,
    message_count INTEGER NOT NULL,
    last_message_at TIMESTAMP WITHOUT TIME ZONE,
    expires_at TIMESTAMP WITHOUT TIME ZONE,
    id BIGINT NOT NULL,
    creator_id BIGINT,
    creator_type VARCHAR(32) NOT NULL,
    updater_id BIGINT,
    updater_type VARCHAR(32),
    updated_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    deleted_at TIMESTAMP WITHOUT TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_agent_session_session_id UNIQUE (session_id)
);

CREATE INDEX idx_agent_session_user_last_message
    ON agent_session (user_id, last_message_at);
CREATE INDEX idx_agent_session_user_status_updated
    ON agent_session (user_id, status, updated_at);

CREATE TABLE celery_task_record (
    task_name VARCHAR(255) NOT NULL,
    trace_id VARCHAR(128) NOT NULL,
    scope VARCHAR(128) NOT NULL,
    queue VARCHAR(64) NOT NULL,
    idempotency_key_hash VARCHAR(64) NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    cancel_allowed BOOLEAN DEFAULT true NOT NULL,
    execution_token VARCHAR(64),
    attempt_count INTEGER DEFAULT 0 NOT NULL,
    queued_deadline_at TIMESTAMP WITHOUT TIME ZONE,
    hard_deadline_at TIMESTAMP WITHOUT TIME ZONE,
    fence_expires_at TIMESTAMP WITHOUT TIME ZONE,
    started_at TIMESTAMP WITHOUT TIME ZONE,
    finished_at TIMESTAMP WITHOUT TIME ZONE,
    error_code VARCHAR(64),
    error_summary VARCHAR(512),
    id BIGINT NOT NULL,
    creator_id BIGINT,
    creator_type VARCHAR(32) NOT NULL,
    updater_id BIGINT,
    updater_type VARCHAR(32),
    updated_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    deleted_at TIMESTAMP WITHOUT TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_celery_task_record_idempotency
        UNIQUE (scope, task_name, idempotency_key_hash),
    CONSTRAINT ck_celery_task_record_status
        CHECK (status IN ('SUBMITTING', 'QUEUED', 'RUNNING', 'CANCELLING',
            'ORPHANED', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    CONSTRAINT ck_celery_task_record_attempt_count
        CHECK (attempt_count >= 0)
);

CREATE INDEX idx_celery_task_record_execution_deadline
    ON celery_task_record (hard_deadline_at, id)
    WHERE status IN ('RUNNING', 'CANCELLING');
CREATE INDEX idx_celery_task_record_orphan_fence
    ON celery_task_record (fence_expires_at, id)
    WHERE status = 'ORPHANED';
CREATE INDEX idx_celery_task_record_queued_deadline
    ON celery_task_record (queued_deadline_at, id)
    WHERE status = 'QUEUED';
CREATE INDEX idx_celery_task_record_scope_status
    ON celery_task_record (scope, status, created_at, id);
CREATE INDEX idx_celery_task_record_scope_trace
    ON celery_task_record (scope, trace_id, created_at DESC, id DESC);
CREATE INDEX idx_celery_task_record_stale_submitting
    ON celery_task_record (updated_at, id)
    WHERE status = 'SUBMITTING';

CREATE TABLE scoped_operation_locks (
    operation_scope VARCHAR(64) NOT NULL,
    resource_key VARCHAR(128) NOT NULL,
    id BIGINT NOT NULL,
    creator_id BIGINT,
    creator_type VARCHAR(32) NOT NULL,
    updater_id BIGINT,
    updater_type VARCHAR(32),
    updated_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    deleted_at TIMESTAMP WITHOUT TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uk_scoped_op_lock_key
        UNIQUE (operation_scope, resource_key)
);

CREATE TABLE external_identity (
    user_id BIGINT NOT NULL,
    provider VARCHAR(32) NOT NULL,
    connection_key VARCHAR(64) NOT NULL,
    subject VARCHAR(256) NOT NULL,
    email_snapshot VARCHAR(255),
    union_id VARCHAR(256),
    nickname_snapshot VARCHAR(128),
    avatar_snapshot VARCHAR(512),
    last_authenticated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    id BIGINT NOT NULL,
    creator_id BIGINT,
    creator_type VARCHAR(32) NOT NULL,
    updater_id BIGINT,
    updater_type VARCHAR(32),
    updated_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    deleted_at TIMESTAMP WITHOUT TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT uq_external_identity_subject
        UNIQUE (provider, connection_key, subject)
);

CREATE INDEX idx_external_identity_user_provider
    ON external_identity (user_id, provider, deleted_at);

CREATE TABLE "user" (
    username VARCHAR(64) NOT NULL,
    account VARCHAR(64) NOT NULL,
    phone VARCHAR(11) NOT NULL,
    password_hash VARCHAR(255),
    id BIGINT NOT NULL,
    creator_id BIGINT,
    creator_type VARCHAR(32) NOT NULL,
    updater_id BIGINT,
    updater_type VARCHAR(32),
    updated_at TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    deleted_at TIMESTAMP WITHOUT TIME ZONE,
    PRIMARY KEY (id)
);
