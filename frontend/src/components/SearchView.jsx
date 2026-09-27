import { useState } from "react";
import { search } from "../api.js";
import ResultCard from "./ResultCard.jsx";
import PreviewModal from "./PreviewModal.jsx";

const EXAMPLES = [
  "A woman standing with a cat",
  "Customer testimonial videos",
  "Brochures related to residential projects",
  "Images showing a modern living room",
  "Videos containing construction activity",
];

const TYPES = [
  { value: "image", label: "Images" },
  { value: "video", label: "Videos" },
  { value: "pdf", label: "PDFs" },
];

const EMPTY_FILTERS = { types: [], folder: "", size: "", duration: "", mode: "hybrid" };

export default function SearchView({ folders }) {
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);
  const [elapsed, setElapsed] = useState(null);

  async function run(q = query, f = filters) {
    if (!q.trim()) return;
    setLoading(true);
    setError("");
    const t0 = performance.now();
    try {
      setData(await search(q, f));
      setElapsed(Math.round(performance.now() - t0));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  function updateFilter(patch) {
    const next = { ...filters, ...patch };
    setFilters(next);
    if (data) run(query, next); // re-run the current search with new filters
  }

  function toggleType(t) {
    const types = filters.types.includes(t) ? filters.types.filter((x) => x !== t) : [...filters.types, t];
    updateFilter({ types });
  }

  return (
    <section>
      <form
        className="searchbar"
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <input
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Describe what you're looking for…"
        />
        <button type="submit" disabled={loading}>
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      <div className="examples">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            className="chip"
            onClick={() => {
              setQuery(ex);
              run(ex);
            }}
          >
            {ex}
          </button>
        ))}
      </div>

      <div className="filters">
        <div className="filter-group">
          {TYPES.map((t) => (
            <label key={t.value} className={`toggle ${filters.types.includes(t.value) ? "on" : ""}`}>
              <input type="checkbox" checked={filters.types.includes(t.value)} onChange={() => toggleType(t.value)} />
              {t.label}
            </label>
          ))}
        </div>

        <select value={filters.folder} onChange={(e) => updateFilter({ folder: e.target.value })}>
          <option value="">All folders</option>
          {folders.map((f) => (
            <option key={f} value={f}>
              {f || "(root)"}
            </option>
          ))}
        </select>

        <select value={filters.size} onChange={(e) => updateFilter({ size: e.target.value })}>
          <option value="">Any size</option>
          <option value="-1048576">&lt; 1 MB</option>
          <option value="1048576-10485760">1 – 10 MB</option>
          <option value="10485760-">&gt; 10 MB</option>
        </select>

        <select value={filters.duration} onChange={(e) => updateFilter({ duration: e.target.value })}>
          <option value="">Any duration</option>
          <option value="-15">Video &lt; 15 s</option>
          <option value="15-60">Video 15 – 60 s</option>
          <option value="60-">Video &gt; 60 s</option>
        </select>

        <select
          value={filters.mode}
          onChange={(e) => updateFilter({ mode: e.target.value })}
          title="Which retrieval signals to use (for comparing quality)"
        >
          <option value="hybrid">Hybrid (all signals)</option>
          <option value="visual">Visual only (CLIP)</option>
          <option value="text">Text meaning only</option>
          <option value="keyword">Keyword only (BM25)</option>
        </select>

        {JSON.stringify(filters) !== JSON.stringify(EMPTY_FILTERS) && (
          <button className="link" onClick={() => updateFilter(EMPTY_FILTERS)}>
            Reset
          </button>
        )}
      </div>

      {error && <div className="error">{error}</div>}

      {data && (
        <div className="result-meta muted small">
          {data.results.length} results for “{data.query}”
          {elapsed != null && ` · ${elapsed} ms`}
          {data.intent.length > 0 && ` · detected intent: ${data.intent.join(", ")} (boosted)`}
        </div>
      )}

      {data && data.results.length === 0 && (
        <div className="empty">No relevant assets found. Try different words or remove filters.</div>
      )}

      <div className="grid">
        {data?.results.map((r, i) => (
          <ResultCard key={r.asset_id} rank={i + 1} result={r} onOpen={() => setSelected(r)} />
        ))}
      </div>

      {selected && <PreviewModal result={selected} onClose={() => setSelected(null)} />}
    </section>
  );
}
