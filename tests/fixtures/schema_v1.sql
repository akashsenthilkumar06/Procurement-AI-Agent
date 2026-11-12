CREATE TABLE decisions (
                    id TEXT PRIMARY KEY, session_id TEXT UNIQUE NOT NULL, message_id TEXT, action TEXT NOT NULL,
                    recommended_option TEXT, final_option TEXT, created_at REAL NOT NULL,
                    client_deliberation_ms REAL, server_since_seen_ms REAL,
                    outcome_score REAL, score_regret REAL, reason TEXT NOT NULL);
CREATE TABLE events (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, type TEXT NOT NULL,
                    received_at REAL NOT NULL, client_ms REAL NOT NULL, client_wall_ms REAL NOT NULL,
                    page_id TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE launches (
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, participant_id TEXT NOT NULL,
                    condition_id TEXT NOT NULL, revision_id INTEGER NOT NULL, expires_at REAL NOT NULL,
                    consumed_at REAL, preview INTEGER NOT NULL);
CREATE TABLE messages (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                    created_at REAL NOT NULL, option_id TEXT, metadata_json TEXT NOT NULL,
                    request_id TEXT NOT NULL, seen_at REAL, seen_client_ms REAL, page_id TEXT);
CREATE TABLE requests (
                    session_id TEXT NOT NULL, request_id TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY(session_id, request_id));
CREATE TABLE revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
                    config_hash TEXT NOT NULL, config_json TEXT NOT NULL);
CREATE TABLE sessions (
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, participant_id TEXT NOT NULL,
                    condition_id TEXT NOT NULL, revision_id INTEGER NOT NULL, config_hash TEXT NOT NULL,
                    created_at REAL NOT NULL, ended_at REAL, status TEXT NOT NULL, preview INTEGER NOT NULL,
                    consent_version TEXT NOT NULL, consent_at REAL NOT NULL);
