-- ST-01 (EP-2026-014): Agent core foundations (agents and agent_runs).
CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    emoji TEXT NOT NULL DEFAULT '🤖',
    system_prompt TEXT NOT NULL,
    tool_allowlist TEXT NOT NULL DEFAULT '[]',
    model TEXT,
    write_mode TEXT NOT NULL CHECK (write_mode IN ('proposal', 'direct')) DEFAULT 'proposal',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_agents_name ON agents (name);

CREATE TABLE agent_runs (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents (id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending_review', 'applied', 'rejected')) DEFAULT 'applied',
    summary TEXT NOT NULL,
    diff_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_agent_runs_agent_created ON agent_runs (agent_id, created_at);

-- Seed default agents when the table is empty
INSERT OR IGNORE INTO agents (id, name, emoji, system_prompt, tool_allowlist, model, write_mode, created_at)
VALUES
    (
        'agent-research-default',
        'Research-Agent',
        '📚',
        'You are Research-Agent, an AI assistant specialized in analyzing research papers and external literature, extracting key takeaways and citations, formulating open questions, and synthesizing domain knowledge across the Personal Graph OS workspace.',
        '[]',
        NULL,
        'proposal',
        datetime('now')
    ),
    (
        'agent-ingest-default',
        'Ingest-Agent',
        '📥',
        'You are Ingest-Agent, an AI assistant specialized in capturing, normalizing, extracting, and indexing external resources, papers, repositories, and documentation into the Personal Graph OS knowledge graph.',
        '[]',
        NULL,
        'proposal',
        datetime('now')
    ),
    (
        'agent-plan-default',
        'Plan-Agent',
        '🗺',
        'You are Plan-Agent, an AI assistant specialized in breaking down high-level epics and strategic objectives into actionable stories, tasks, checklists, and execution roadmaps within Personal Graph OS.',
        '[]',
        NULL,
        'proposal',
        datetime('now')
    );
