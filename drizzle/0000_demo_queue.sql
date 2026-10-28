CREATE TABLE sessions (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, visitor TEXT NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL);
--> statement-breakpoint
CREATE TABLE jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, created INTEGER NOT NULL, claimed INTEGER, result TEXT);
--> statement-breakpoint
CREATE INDEX jobs_state_created ON jobs(state,created);
--> statement-breakpoint
CREATE INDEX jobs_owner ON jobs(owner,created);
--> statement-breakpoint
CREATE TABLE control (name TEXT PRIMARY KEY, value TEXT NOT NULL);
