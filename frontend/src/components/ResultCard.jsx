import { formatBytes, formatDuration, formatLocator, SIGNAL_LABELS } from "../format.js";

export default function ResultCard({ result, rank, onOpen }) {
  const r = result;
  return (
    <article className="card" onClick={onOpen}>
      <div className="thumb">
        {r.thumbnail_url ? <img src={r.thumbnail_url} alt={r.filename} loading="lazy" /> : <div className="nothumb">{r.file_type}</div>}
        <span className={`badge ${r.file_type}`}>{r.file_type.toUpperCase()}</span>
        {r.duration_sec != null && <span className="duration">{formatDuration(r.duration_sec)}</span>}
        <span className="rank">#{rank}</span>
      </div>
      <div className="card-body">
        <div className="filename" title={r.path}>
          {r.filename}
        </div>
        <div className="muted small">
          {r.folder || "(root)"} · {formatBytes(r.size_bytes)}
          {r.page_count ? ` · ${r.page_count} pages` : ""}
          {r.duplicates.length > 0 && ` · +${r.duplicates.length} duplicate`}
        </div>
        <div className="why">
          {Object.entries(r.matches).map(([signal, m]) => (
            <span key={signal} className={`why-chip ${signal}`} title={m.snippet || ""}>
              {SIGNAL_LABELS[signal]} {formatLocator(m.kind, m.locator)}
            </span>
          ))}
        </div>
        {bestSnippet(r) && <div className="snippet">“{bestSnippet(r)}”</div>}
      </div>
    </article>
  );
}

function bestSnippet(r) {
  return r.matches.keyword?.snippet || r.matches.text?.snippet || null;
}
