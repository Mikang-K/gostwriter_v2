CREATE SCHEMA IF NOT EXISTS ghostwriter;
CREATE TABLE IF NOT EXISTS ghostwriter.schema_migrations (
    version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ghostwriter.projects (
    id uuid PRIMARY KEY, owner_id uuid NOT NULL,
    settings jsonb NOT NULL, revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    head_memory_revision bigint NOT NULL DEFAULT 0,
    archived boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(id, owner_id)
);
CREATE TABLE IF NOT EXISTS ghostwriter.characters (
    project_id uuid NOT NULL, owner_id uuid NOT NULL, id uuid NOT NULL,
    position integer NOT NULL CHECK (position >= 0), data jsonb NOT NULL,
    PRIMARY KEY(project_id, id),
    FOREIGN KEY(project_id, owner_id) REFERENCES ghostwriter.projects(id, owner_id)
);
CREATE TABLE IF NOT EXISTS ghostwriter.memory_revisions (
    project_id uuid NOT NULL, owner_id uuid NOT NULL, revision bigint NOT NULL CHECK (revision >= 0),
    memory jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(project_id, revision),
    FOREIGN KEY(project_id, owner_id) REFERENCES ghostwriter.projects(id, owner_id)
);
CREATE OR REPLACE FUNCTION ghostwriter.immutable_memory() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'memory revisions are immutable'; END;
$$;
CREATE TRIGGER immutable_memory BEFORE UPDATE OR DELETE ON ghostwriter.memory_revisions
    FOR EACH ROW EXECUTE FUNCTION ghostwriter.immutable_memory();
ALTER TABLE ghostwriter.projects ADD CONSTRAINT projects_head_memory_fk
    FOREIGN KEY(id, head_memory_revision) REFERENCES ghostwriter.memory_revisions(project_id, revision)
    DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE IF NOT EXISTS ghostwriter.generation_requests (
    id uuid PRIMARY KEY, project_id uuid NOT NULL, owner_id uuid NOT NULL,
    request_id text NOT NULL, idempotency_key uuid NOT NULL, payload_hash text NOT NULL,
    input jsonb NOT NULL, base_revision bigint NOT NULL CHECK (base_revision >= 0),
    status text NOT NULL CHECK (status IN ('pending','running','finalizing','succeeded','failed','cancelled','interrupted')),
    run_epoch integer NOT NULL DEFAULT 1 CHECK (run_epoch > 0),
    draft_body text NOT NULL DEFAULT '', draft_complete boolean NOT NULL DEFAULT false,
    cancel_requested boolean NOT NULL DEFAULT false,
    error_code text, metrics jsonb NOT NULL DEFAULT '{}', committed_episode_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(), heartbeat_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz, undone_at timestamptz,
    UNIQUE(owner_id, idempotency_key), UNIQUE(id, project_id),
    FOREIGN KEY(project_id, owner_id) REFERENCES ghostwriter.projects(id, owner_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_generation ON ghostwriter.generation_requests(project_id)
    WHERE status IN ('pending','running','finalizing');
CREATE INDEX IF NOT EXISTS generation_owner_created ON ghostwriter.generation_requests(owner_id, created_at);
CREATE TABLE IF NOT EXISTS ghostwriter.episodes (
    id uuid PRIMARY KEY, project_id uuid NOT NULL, owner_id uuid NOT NULL,
    generation_id uuid NOT NULL UNIQUE, episode_number integer NOT NULL CHECK (episode_number > 0),
    body text NOT NULL, summary text NOT NULL,
    before_memory_revision bigint NOT NULL, after_memory_revision bigint NOT NULL,
    deleted_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(id, project_id),
    FOREIGN KEY(project_id, owner_id) REFERENCES ghostwriter.projects(id, owner_id),
    FOREIGN KEY(generation_id, project_id) REFERENCES ghostwriter.generation_requests(id, project_id),
    FOREIGN KEY(project_id, before_memory_revision) REFERENCES ghostwriter.memory_revisions(project_id, revision),
    FOREIGN KEY(project_id, after_memory_revision) REFERENCES ghostwriter.memory_revisions(project_id, revision),
    CHECK(after_memory_revision > before_memory_revision)
);
CREATE UNIQUE INDEX IF NOT EXISTS live_episode_number ON ghostwriter.episodes(project_id, episode_number)
    WHERE deleted_at IS NULL;
ALTER TABLE ghostwriter.generation_requests ADD CONSTRAINT request_committed_episode_fk
    FOREIGN KEY(committed_episode_id, project_id) REFERENCES ghostwriter.episodes(id, project_id)
    DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE IF NOT EXISTS ghostwriter.sessions (
    token_hash text PRIMARY KEY, owner_id uuid NOT NULL, expires_at timestamptz NOT NULL
);
INSERT INTO ghostwriter.schema_migrations(version) VALUES (1);
