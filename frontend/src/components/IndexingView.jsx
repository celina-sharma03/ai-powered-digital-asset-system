import { useEffect, useRef, useState } from "react";
import { cancelIndex, getIndexIssues, getIndexStatus, startIndex } from "../api.js";
import { formatBytes } from "../format.js";

export default function IndexingView({ stats, onRunFinished }) {
  const [status, setStatus] = useState(null);
  const [issues, setIssues] = useState(null);
  const [error, setError] = useState("");
  const wasRunning = useRef(false);

  async function refresh() {
    try {
      const s = await getIndexStatus();
      setStatus(s);
      if (wasRunning.current && !s.running) {
        onRunFinished?.();
        getIndexIssues().then(setIssues);
      }
      wasRunning.current = s.running;
    } catch (e) {
      setError(e.message);
    }
  }

  // Poll quickly while a run is active, slowly otherwise.
  useEffect(() => {
    refresh();
    getIndexIssues().then(setIssues).catch(() => {});
    const id = setInterval(refresh, status?.running ? 1000 : 4000);
    return () => clearInterval(id);
  }, [status?.running]);

  async function start(full) {
    setError("");
    try {
      await startIndex(full);
      refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  const run = status?.run;
  const pct = run?.to_process ? Math.round((100 * run.processed) / run.to_process) : run?.status === "completed" ? 100 : 0;

  return (
    <section className="indexing">
      <div className="panel">
        <div className="panel-head">
          <div>
            <h3>Media folder</h3>
            <code>{stats?.media_dir}</code>
          </div>
          <div className="actions">
            <button onClick={() => start(false)} disabled={status?.running}>
              Scan &amp; index changes
            </button>
            <button className="secondary" onClick={() => start(true)} disabled={status?.running}
              title="Reprocess every file, ignoring the unchanged-file shortcut">
              Full re-index
            </button>
            {status?.running && (
              <button className="danger" onClick={cancelIndex}>
                Cancel
              </button>
            )}
          </div>
        </div>
        {error && <div className="error">{error}</div>}

        {run && (
          <>
            <div className="run-line">
              Run #{run.id} · <b className={`st ${run.status}`}>{run.status}</b>
              {run.phase_detail && <span className="muted"> · {run.phase_detail}</span>}
            </div>
            <div className="progress">
              <div style={{ width: `${pct}%` }} />
            </div>
            <div className="muted small">
              {run.processed}/{run.to_process} processed ({pct}%)
              {run.current_file && ` · now: ${run.current_file}`}
            </div>
            {run.error && <div className="error">{run.error}</div>}

            <div className="counters">
              <Counter label="Files seen" value={run.files_seen} />
              <Counter label="Unchanged (skipped)" value={run.unchanged} />
              <Counter label="New assets" value={run.new_assets} />
              <Counter label="Duplicates linked" value={run.duplicates} />
              <Counter label="Unsupported" value={run.unsupported} />
              <Counter label="Removed" value={run.removed} />
              <Counter label="Failed this run" value={run.failed} />
            </div>
          </>
        )}
      </div>

      <div className="panel">
        <h3>Library</h3>
        <div className="counters">
          {stats?.by_type?.map((t) => (
            <Counter key={t.file_type} label={`${t.file_type} · ${formatBytes(t.bytes)}`} value={t.count} />
          ))}
          {status &&
            Object.entries(status.assets_by_status).map(([k, v]) => <Counter key={k} label={`status: ${k}`} value={v} />)}
          {stats && <Counter label="Assets with duplicate copies" value={stats.assets_with_duplicates} />}
          {stats && <Counter label="Visual vectors" value={stats.vectors.visual} />}
          {stats && <Counter label="Text vectors" value={stats.vectors.text} />}
        </div>
      </div>

      <div className="panel">
        <h3>Problems ({(issues?.assets.length ?? 0) + (issues?.files.length ?? 0)})</h3>
        <p className="muted small">
          Failed files are retried automatically on later runs (up to the configured max attempts). “Partial” means the
          file is searchable but an optional AI step (OCR, transcript, caption) failed.
        </p>
        <table className="issues">
          <thead>
            <tr><th>File</th><th>Status</th><th>Reason</th></tr>
          </thead>
          <tbody>
            {issues?.assets.map((a) => (
              <tr key={`a${a.id}`}>
                <td>{a.rel_path}</td>
                <td><span className={`st ${a.status}`}>{a.status}</span> (attempt {a.attempts})</td>
                <td>{a.error}</td>
              </tr>
            ))}
            {issues?.files.map((f) => (
              <tr key={f.rel_path}>
                <td>{f.rel_path}</td>
                <td><span className={`st ${f.status}`}>{f.status}</span></td>
                <td>{f.error}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Counter({ label, value }) {
  return (
    <div className="counter">
      <div className="value">{value ?? 0}</div>
      <div className="label">{label}</div>
    </div>
  );
}
