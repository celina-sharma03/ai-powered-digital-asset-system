import { useEffect, useState } from "react";
import { getAsset, revealFile } from "../api.js";
import { formatBytes, formatDuration, formatLocator, SIGNAL_LABELS } from "../format.js";

export default function PreviewModal({ result, onClose }) {
  const [detail, setDetail] = useState(null);
  const [message, setMessage] = useState("");
  const r = result;

  useEffect(() => {
    getAsset(r.asset_id).then(setDetail).catch(() => {});
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [r.asset_id]);

  // Open the preview where the match was found: video timestamp or PDF page.
  const loc = r.jump_to?.locator;
  let preview;
  if (r.file_type === "image") {
    preview = <img src={r.content_url} alt={r.filename} />;
  } else if (r.file_type === "video") {
    const t = typeof loc === "number" && ["frame", "transcript"].includes(r.jump_to.kind) ? loc : 0;
    preview = <video src={`${r.content_url}#t=${t}`} controls autoPlay muted />;
  } else {
    const page = typeof loc === "number" && ["page", "pdf_text", "ocr"].includes(r.jump_to.kind) ? loc : 1;
    preview = <iframe src={`${r.content_url}#page=${page}`} title={r.filename} />;
  }

  async function reveal() {
    try {
      await revealFile(r.asset_id);
      setMessage("Opened in file explorer");
    } catch (e) {
      setMessage(e.message);
    }
  }

  function copyPath() {
    navigator.clipboard.writeText(r.path);
    setMessage("Path copied");
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <strong>{r.filename}</strong>
          <button className="link" onClick={onClose}>Close ✕</button>
        </div>

        <div className="modal-body">
          <div className="preview">{preview}</div>

          <aside className="details">
            <h4>Location</h4>
            <code className="path">{r.path}</code>
            <div className="actions">
              <button onClick={reveal}>Show in folder</button>
              <button onClick={copyPath}>Copy path</button>
              <a className="button" href={r.content_url} target="_blank" rel="noreferrer">Open file</a>
            </div>
            {message && <div className="muted small">{message}</div>}
            {r.duplicates.length > 0 && (
              <>
                <h4>Duplicate copies (same content)</h4>
                {r.duplicates.map((p) => <code key={p} className="path">{p}</code>)}
              </>
            )}

            <h4>Why this matched</h4>
            <ul className="matches">
              {Object.entries(r.matches).map(([signal, m]) => (
                <li key={signal}>
                  <b>{SIGNAL_LABELS[signal]}</b> · rank {m.rank} · score {m.score} · {m.kind} {formatLocator(m.kind, m.locator)}
                  {m.snippet && <div className="snippet">“{m.snippet}”</div>}
                </li>
              ))}
            </ul>

            <h4>Metadata</h4>
            <table className="meta">
              <tbody>
                <tr><td>Type</td><td>{r.file_type}</td></tr>
                <tr><td>Size</td><td>{formatBytes(r.size_bytes)}</td></tr>
                {r.width && <tr><td>Resolution</td><td>{r.width} × {r.height}</td></tr>}
                {r.duration_sec != null && <tr><td>Duration</td><td>{formatDuration(r.duration_sec)}</td></tr>}
                {r.page_count && <tr><td>Pages</td><td>{r.page_count}</td></tr>}
                {r.title && <tr><td>Title</td><td>{r.title}</td></tr>}
                <tr><td>Status</td><td>{r.status}</td></tr>
                {detail?.content_hash && <tr><td>SHA-256</td><td className="hash">{detail.content_hash.slice(0, 16)}…</td></tr>}
                {detail?.stages && Object.entries(detail.stages).map(([k, v]) => (
                  <tr key={k}><td>{k}</td><td>{String(v)}</td></tr>
                ))}
              </tbody>
            </table>

            {r.summary && (
              <>
                <h4>Extracted content</h4>
                <p className="summary">{r.summary}</p>
              </>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}
