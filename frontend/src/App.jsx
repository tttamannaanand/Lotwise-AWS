import { useCallback, useEffect, useRef, useState } from "react";
import { api, fileToCsvText, loadConfig, resetWorkspaceId } from "./api.js";
import LotStrip from "./components/LotStrip.jsx";
import MappingReview from "./components/MappingReview.jsx";
import Report from "./components/Report.jsx";

const SAMPLES = ["broker_tradebook.csv", "mf_statement.csv", "crypto_exchange.csv"];
const ACCEPT = ".csv,.txt,.tsv,.xlsx,.xls";

const SOURCE_SHORT = { ai: "AI", template: "saved layout", keywords: "column names" };

export default function App() {
  const [ready, setReady] = useState(false);
  const [fatal, setFatal] = useState(null);
  const [pending, setPending] = useState([]);
  const [reading, setReading] = useState([]);
  const [imports, setImports] = useState([]);
  const [report, setReport] = useState(null);
  const [fy, setFy] = useState(null);
  const [loadingReport, setLoadingReport] = useState(false);
  const [error, setError] = useState(null);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef(null);
  const previewTimers = useRef({});

  const refresh = useCallback(async (year) => {
    setLoadingReport(true);
    try {
      const [list, rep] = await Promise.all([api.listImports(), api.report(year)]);
      setImports(list.imports.filter((i) => i.status === "confirmed"));
      setReport(rep.empty ? null : rep);
      if (!rep.empty) setFy(rep.fy);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingReport(false);
    }
  }, []);

  useEffect(() => {
    loadConfig()
      .then(() => {
        setReady(true);
        return refresh();
      })
      .catch((e) => setFatal(e.message));
  }, [refresh]);

  // ------------------------------------------------------------- importing

  async function startImport(filename, getText) {
    const token = `${filename}-${Math.random()}`;
    setReading((r) => [...r, { token, filename }]);
    try {
      const text = await getText();
      const proposal = await api.createImport(filename, text);
      setPending((p) => [...p, { ...proposal, busy: false, error: null }]);
    } catch (e) {
      setError(`${filename}: ${e.message}`);
    } finally {
      setReading((r) => r.filter((x) => x.token !== token));
    }
  }

  function handleFiles(fileList) {
    setError(null);
    Array.from(fileList || []).forEach((f) => startImport(f.name, () => fileToCsvText(f)));
  }

  function loadSamples() {
    setError(null);
    SAMPLES.forEach((name) =>
      startImport(name, async () => {
        const res = await fetch(`/samples/${name}`);
        if (!res.ok) throw new Error("Sample file missing from this deployment.");
        return res.text();
      }),
    );
  }

  const patch = (id, changes) => setPending((p) => p.map((x) => (x.importId === id ? { ...x, ...changes } : x)));

  function changeMapping(id, mapping) {
    patch(id, { mapping });
    clearTimeout(previewTimers.current[id]);
    previewTimers.current[id] = setTimeout(async () => {
      try {
        const r = await api.preview(id, mapping);
        patch(id, { preview: r.preview, rowCount: r.rowCount, legCount: r.legCount, errorCount: r.errorCount, errors: r.errors, error: null });
      } catch (e) {
        patch(id, { error: e.message });
      }
    }, 300);
  }

  async function confirm(id) {
    const item = pending.find((x) => x.importId === id);
    if (!item) return;
    patch(id, { busy: true, error: null });
    try {
      await api.confirm(id, item.mapping);
      setPending((p) => p.filter((x) => x.importId !== id));
      await refresh(fy);
    } catch (e) {
      patch(id, { busy: false, error: e.message });
    }
  }

  async function confirmAll() {
    const batch = pending.filter((x) => x.legCount > 0 && !x.busy);
    const ids = new Set(batch.map((x) => x.importId));
    setPending((p) => p.map((x) => (ids.has(x.importId) ? { ...x, busy: true } : x)));
    const failed = {};
    for (const item of batch) {
      try {
        await api.confirm(item.importId, item.mapping);
      } catch (e) {
        failed[item.importId] = e.message;
      }
    }
    setPending((p) =>
      p
        .filter((x) => !ids.has(x.importId) || failed[x.importId])
        .map((x) => (failed[x.importId] ? { ...x, busy: false, error: failed[x.importId] } : x)),
    );
    await refresh(fy);
  }

  async function discard(id) {
    setPending((p) => p.filter((x) => x.importId !== id));
    api.deleteImport(id).catch(() => {});
  }

  async function removeImport(id) {
    setImports((list) => list.filter((i) => i.importId !== id));
    try {
      await api.deleteImport(id);
    } finally {
      await refresh();
    }
  }

  async function clearAll() {
    if (!window.confirm("Delete all your uploaded files and results from Lotwise?")) return;
    try {
      await api.clearWorkspace();
    } catch {
      /* start fresh regardless */
    }
    resetWorkspaceId();
    setPending([]);
    setImports([]);
    setReport(null);
    setFy(null);
  }

  // ---------------------------------------------------------------- render

  if (fatal) {
    return (
      <main className="shell">
        <p className="note note--error" role="alert">{fatal}</p>
      </main>
    );
  }

  const empty = imports.length === 0 && pending.length === 0 && reading.length === 0;
  const readyCount = pending.filter((x) => x.legCount > 0).length;

  return (
    <>
      <header className="topbar">
        <div className="shell topbar__inner">
          <a className="wordmark" href="/">
            <span className="wordmark__mark" aria-hidden="true"><i className="lot--LT" /><i className="lot--ST" /></span>
            Lotwise
          </a>
          {!empty && <button className="btn btn--link" onClick={clearAll}>Delete my data</button>}
        </div>
      </header>

      <main className="shell">
        {empty && (
          <section className="hero">
            <div className="hero__copy">
              <h1>Every sale, matched to the purchase it came from</h1>
              <p className="lede">
                Upload tradebooks from any Indian broker, mutual fund statement or crypto exchange. Lotwise reads the columns with AI, matches each sale to its purchase lots first-in-first-out, and applies this year's capital gains rules.
              </p>
            </div>
            <figure className="hero__figure">
              <figcaption>Sold 500 shares on 17 Jul 2025</figcaption>
              <LotStrip
                size="xl"
                showLabels
                parts={[
                  { quantity: "400", buy_date: "2024-03-11", term: "LT", gain: "173240" },
                  { quantity: "100", buy_date: "2024-11-05", term: "ST", gain: "35750" },
                ]}
              />
              <p className="hero__explain">
                One sale, two tax treatments. The first 400 shares were held over a year and are taxed at 12.5% after the ₹1.25 lakh exemption. The last 100 weren't, so they're taxed at 20%.
              </p>
            </figure>
          </section>
        )}

        <section className="step" aria-labelledby="step-1">
          <h2 id="step-1" className="step__title"><span className="step__n">1</span>Add your statements</h2>
          <div
            className={`drop ${dragging ? "is-dragging" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => { e.preventDefault(); setDragging(false); handleFiles(e.dataTransfer.files); }}
          >
            <p className="drop__main">Drop CSV or Excel tradebooks here, from as many brokers and exchanges as you use.</p>
            <div className="drop__actions">
              <button className="btn btn--primary" onClick={() => fileInput.current?.click()} disabled={!ready}>Choose files</button>
              <button className="btn btn--ghost" onClick={loadSamples} disabled={!ready}>Try three sample statements</button>
            </div>
            <input ref={fileInput} type="file" multiple accept={ACCEPT} hidden onChange={(e) => { handleFiles(e.target.files); e.target.value = ""; }} />
            <p className="drop__fine">
              Only column names and the first five rows are sent to the AI, and never used to calculate tax. Your files are deleted automatically after 14 days.
            </p>
          </div>
          {error && <p className="note note--error" role="alert">{error}</p>}
          {reading.map((r) => (
            <p key={r.token} className="reading" aria-live="polite"><span className="spinner" aria-hidden="true" />Reading the columns in {r.filename}</p>
          ))}
          {imports.length > 0 && (
            <ul className="files" aria-label="Imported files">
              {imports.map((i) => (
                <li key={i.importId}>
                  <span className="files__name">{i.filename}</span>
                  <span className="files__meta">
                    {i.assetClass === "crypto" ? "Crypto" : "Stocks and funds"}, {i.legCount} entries, mapped by {SOURCE_SHORT[i.source] || "column names"}
                  </span>
                  <button className="btn btn--link" onClick={() => removeImport(i.importId)} aria-label={`Remove ${i.filename}`}>Remove</button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {pending.length > 0 && (
          <section className="step" aria-labelledby="step-2">
            <div className="step__bar">
              <h2 id="step-2" className="step__title"><span className="step__n">2</span>Check the columns</h2>
              {readyCount > 1 && <button className="btn btn--primary" onClick={confirmAll}>Import all {readyCount} files</button>}
            </div>
            <p className="step__intro">Lotwise guessed what each column means. Fix anything that looks wrong, then import.</p>
            {pending.map((item) => (
              <MappingReview
                key={item.importId}
                item={item}
                onChange={(m) => changeMapping(item.importId, m)}
                onConfirm={() => confirm(item.importId)}
                onDiscard={() => discard(item.importId)}
              />
            ))}
          </section>
        )}

        {report && (
          <section className="step" aria-labelledby="step-3">
            <h2 id="step-3" className="step__title"><span className="step__n">3</span>Your capital gains</h2>
            <Report report={report} loading={loadingReport} onYear={(y) => { setFy(y); refresh(y); }} />
          </section>
        )}
      </main>

      <footer className="footer">
        <div className="shell">
          <p>
            Lotwise gives estimates for information only and isn't tax advice. Check the figures with a chartered accountant or the Income Tax Department's utilities before you file.
          </p>
        </div>
      </footer>
    </>
  );
}
