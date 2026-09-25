"""Add the external inventory without changing managed artifacts."""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.execute('\nCREATE TABLE external_entries (\n\tid VARCHAR(36) NOT NULL, \n\tproject_id VARCHAR(36) NOT NULL, \n\tname VARCHAR(1024) NOT NULL, \n\tformat VARCHAR(32) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\talias_id VARCHAR(36), \n\tcreated_at DATETIME NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, \n\tFOREIGN KEY(alias_id) REFERENCES external_entries (id) ON DELETE CASCADE\n)\n\n')
    op.execute('CREATE INDEX ix_external_entries_project_id ON external_entries (project_id)')
    op.execute('\nCREATE TABLE external_revisions (\n\tid VARCHAR(36) NOT NULL, \n\tentry_id VARCHAR(36) NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\tfingerprint VARCHAR(96) NOT NULL, \n\tbyte_size INTEGER NOT NULL, \n\tmanifest JSON, \n\tcreated_at DATETIME NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(entry_id) REFERENCES external_entries (id) ON DELETE CASCADE\n)\n\n')
    op.execute('CREATE INDEX ix_external_revisions_entry_id ON external_revisions (entry_id)')
    op.execute('CREATE INDEX ix_external_revisions_fingerprint ON external_revisions (fingerprint)')
    op.execute('\nCREATE TABLE external_roots (\n\tid VARCHAR(36) NOT NULL, \n\tproject_id VARCHAR(36) NOT NULL, \n\tagent_id VARCHAR(36) NOT NULL, \n\tlabel VARCHAR(255) NOT NULL, \n\tpath VARCHAR(2048) NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\tidentity VARCHAR(255), \n\tstatus VARCHAR(32) NOT NULL, \n\tlast_seen_at DATETIME, \n\terror TEXT, \n\tcreated_at DATETIME NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, \n\tFOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE\n)\n\n')
    op.execute('CREATE INDEX ix_external_roots_agent_id ON external_roots (agent_id)')
    op.execute('CREATE INDEX ix_external_roots_project_id ON external_roots (project_id)')
    op.execute('\nCREATE TABLE external_locations (\n\tid VARCHAR(36) NOT NULL, \n\troot_id VARCHAR(36) NOT NULL, \n\tentry_id VARCHAR(36) NOT NULL, \n\trelative_path VARCHAR(2048) NOT NULL, \n\trevision_id VARCHAR(36), \n\tstatus VARCHAR(32) NOT NULL, \n\treadiness VARCHAR(32) NOT NULL, \n\tsignature VARCHAR(255) NOT NULL, \n\tbyte_size INTEGER NOT NULL, \n\tlast_scan_id VARCHAR(36), \n\tobserved_at DATETIME NOT NULL, \n\tverified_at DATETIME, \n\tcreated_at DATETIME NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (root_id, relative_path), \n\tFOREIGN KEY(root_id) REFERENCES external_roots (id) ON DELETE CASCADE, \n\tFOREIGN KEY(entry_id) REFERENCES external_entries (id) ON DELETE CASCADE, \n\tFOREIGN KEY(revision_id) REFERENCES external_revisions (id) ON DELETE SET NULL\n)\n\n')
    op.execute('CREATE INDEX ix_external_locations_entry_id ON external_locations (entry_id)')
    op.execute('CREATE INDEX ix_external_locations_root_id ON external_locations (root_id)')
    op.execute('\nCREATE TABLE external_tasks (\n\trequest_key VARCHAR(128), \n\tid VARCHAR(36) NOT NULL, \n\troot_id VARCHAR(36) NOT NULL, \n\tlocation_id VARCHAR(36), \n\trevision_id VARCHAR(36), \n\texperiment_id VARCHAR(36), \n\tartifact_id VARCHAR(36), \n\tkind VARCHAR(16) NOT NULL, \n\tstate VARCHAR(32) NOT NULL, \n\tsequence INTEGER NOT NULL, \n\tpayload JSON NOT NULL, \n\terror TEXT, \n\tcreated_at DATETIME NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (root_id, request_key), \n\tFOREIGN KEY(root_id) REFERENCES external_roots (id) ON DELETE CASCADE, \n\tFOREIGN KEY(location_id) REFERENCES external_locations (id) ON DELETE CASCADE, \n\tFOREIGN KEY(revision_id) REFERENCES external_revisions (id) ON DELETE SET NULL, \n\tFOREIGN KEY(experiment_id) REFERENCES experiments (id) ON DELETE SET NULL, \n\tFOREIGN KEY(artifact_id) REFERENCES artifacts (id) ON DELETE SET NULL\n)\n\n')
    op.execute('CREATE INDEX ix_external_tasks_root_id ON external_tasks (root_id)')
    op.execute('\nCREATE TABLE external_observations (\n\tid VARCHAR(36) NOT NULL, \n\tlocation_id VARCHAR(36) NOT NULL, \n\ttask_id VARCHAR(36), \n\tfacts JSON NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tupdated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(location_id) REFERENCES external_locations (id) ON DELETE CASCADE, \n\tFOREIGN KEY(task_id) REFERENCES external_tasks (id) ON DELETE SET NULL\n)\n\n')
    op.execute('CREATE INDEX ix_external_observations_location_id ON external_observations (location_id)')


def downgrade():
    op.drop_table("external_observations")
    op.drop_table("external_tasks")
    op.drop_table("external_locations")
    op.drop_table("external_roots")
    op.drop_table("external_revisions")
    op.drop_table("external_entries")
