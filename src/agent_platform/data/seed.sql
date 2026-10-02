-- Illustrative maintenance history (SQLite) used by the reporting tools.
CREATE TABLE assets (asset_id TEXT PRIMARY KEY, tenant TEXT NOT NULL, description TEXT NOT NULL, criticality TEXT NOT NULL);
CREATE TABLE work_orders (
  wo_num TEXT PRIMARY KEY, tenant TEXT NOT NULL, asset_id TEXT NOT NULL REFERENCES assets(asset_id),
  work_type TEXT NOT NULL, status TEXT NOT NULL, priority INTEGER NOT NULL, description TEXT NOT NULL,
  reported_at TEXT NOT NULL, problem_code TEXT, cost REAL NOT NULL DEFAULT 0
);
CREATE TABLE downtime (asset_id TEXT NOT NULL, tenant TEXT NOT NULL, started_at TEXT NOT NULL, hours REAL NOT NULL, planned INTEGER NOT NULL);

INSERT INTO assets VALUES
  ('P-101', 'acme', 'Boiler feed pump 101', 'A'),
  ('P-102', 'acme', 'Boiler feed pump 102 (standby)', 'B'),
  ('AHU-1', 'acme', 'Air handling unit 1', 'C'),
  ('C-900', 'globex', 'Compressor 900', 'A');

INSERT INTO work_orders VALUES
  ('WO-1001', 'acme', 'P-101', 'CM', 'CLOSED', 1, 'Seal leak', '2026-02-10', 'LEAK', 360),
  ('WO-1002', 'acme', 'P-101', 'CM', 'CLOSED', 2, 'High vibration', '2026-05-20', 'VIBRATION', 520),
  ('WO-1003', 'acme', 'P-101', 'EM', 'CLOSED', 1, 'Seal failure', '2026-08-01', 'LEAK', 240),
  ('WO-1004', 'acme', 'P-101', 'PM', 'CLOSED', 3, 'Quarterly inspection', '2026-04-08', NULL, 120),
  ('WO-1005', 'acme', 'AHU-1', 'PM', 'CLOSED', 4, 'Filter replacement', '2026-08-30', NULL, 140),
  ('WO-1006', 'acme', 'P-101', 'PM', 'SCHEDULED', 3, 'Quarterly inspection', '2026-09-28', NULL, 0),
  ('WO-1007', 'acme', 'AHU-1', 'CM', 'WAITING_MATERIAL', 3, 'Fan belt noise', '2026-09-20', NULL, 0),
  ('WO-2001', 'globex', 'C-900', 'CM', 'IN_PROGRESS', 1, 'Oil leak', '2026-09-25', 'LEAK', 0);

INSERT INTO downtime VALUES
  ('P-101', 'acme', '2026-02-10', 5.0, 0), ('P-101', 'acme', '2026-05-20', 7.0, 0),
  ('P-101', 'acme', '2026-08-01', 2.5, 0), ('P-101', 'acme', '2026-04-08', 2.0, 1);
