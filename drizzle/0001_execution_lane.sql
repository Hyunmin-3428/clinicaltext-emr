ALTER TABLE jobs ADD COLUMN execution TEXT NOT NULL DEFAULT 'live';
--> statement-breakpoint
CREATE INDEX jobs_execution_state ON jobs(execution,state,created);
