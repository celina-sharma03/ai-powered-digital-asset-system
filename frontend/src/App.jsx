import { useEffect, useState } from "react";
import { getStats } from "./api.js";
import SearchView from "./components/SearchView.jsx";
import IndexingView from "./components/IndexingView.jsx";

export default function App() {
  const [tab, setTab] = useState("search");
  const [stats, setStats] = useState(null);

  const refreshStats = () => getStats().then(setStats).catch(() => {});
  useEffect(() => {
    refreshStats();
  }, [tab]);

  const total = stats?.by_type?.reduce((n, t) => n + t.count, 0) ?? 0;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">Asset Search</div>
        <nav className="tabs">
          <button className={tab === "search" ? "active" : ""} onClick={() => setTab("search")}>
            Search
          </button>
          <button className={tab === "index" ? "active" : ""} onClick={() => setTab("index")}>
            Indexing
          </button>
        </nav>
        <div className="muted small">
          {stats ? `${total} assets indexed` : "connecting…"}
        </div>
      </header>

      <main>
        {tab === "search" ? (
          <SearchView folders={stats?.folders ?? []} />
        ) : (
          <IndexingView stats={stats} onRunFinished={refreshStats} />
        )}
      </main>
    </div>
  );
}
