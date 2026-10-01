import { useMemo, useState } from "react";
import { day, download, fyLabel, pct, qty, rupees, signedRupees, transfersToCsv } from "../format.js";
import LotStrip from "./LotStrip.jsx";

function Row({ label, value, strong, muted }) {
  return (
    <div className={`ledger__row ${strong ? "is-strong" : ""} ${muted ? "is-muted" : ""}`}>
      <span>{label}</span>
      <span className="ledger__value">{value}</span>
    </div>
  );
}

function TaxStrip({ tax }) {
  const parts = [
    { key: "ST", v: Number(tax.equity_st), label: "Short-term equity" },
    { key: "LT", v: Number(tax.equity_lt), label: "Long-term equity" },
    { key: "VDA", v: Number(tax.vda), label: "Crypto" },
    { key: "cess", v: Number(tax.cess), label: "Cess" },
  ].filter((p) => p.v > 0);
  const total = parts.reduce((s, p) => s + p.v, 0);
  if (!total) return null;
  return (
    <div className="taxstrip">
      <div className="lotstrip lotstrip--lg" role="img" aria-label={parts.map((p) => `${p.label} ${rupees(p.v)}`).join(", ")}>
        {parts.map((p) => (
          <span key={p.key} className={`lot lot--${p.key}`} style={{ flexBasis: `${(p.v / total) * 100}%` }} />
        ))}
      </div>
      <ul className="taxstrip__legend">
        {parts.map((p) => (
          <li key={p.key}><i className={`swatch lot--${p.key}`} aria-hidden="true" />{p.label} <b>{rupees(p.v)}</b></li>
        ))}
      </ul>
    </div>
  );
}

export default function Report({ report, onYear, loading }) {
  const [filter, setFilter] = useState("all");
  const { summary, transfers, holdings, warnings, years, fy } = report;

  const shown = useMemo(
    () => transfers.filter((t) => filter === "all" || (filter === "crypto" ? t.asset_class === "crypto" : t.asset_class === "equity")),
    [transfers, filter],
  );
  if (!summary) return null;

  const eq = summary.equity;
  const tax = summary.tax;
  const hasCrypto = transfers.some((t) => t.asset_class === "crypto");
  const hasEquity = transfers.some((t) => t.asset_class === "equity");
  const exemption = tax ? Number(tax.rates.ltcg_exemption) : 125000;
  const exemptionUsed = Number(eq.ltcg_exemption_used || 0);

  return (
    <div className={`report ${loading ? "is-loading" : ""}`}>
      {years.length > 1 && (
        <nav className="years" aria-label="Financial year">
          {years.map((y) => (
            <button key={y} className={y === fy ? "is-on" : ""} aria-pressed={y === fy} onClick={() => onYear(y)}>
              {fyLabel(y)}
            </button>
          ))}
        </nav>
      )}

      <div className="verdict">
        <p className="verdict__context">
          {fyLabel(fy)}, {summary.transfer_count} {summary.transfer_count === 1 ? "sale" : "sales"}
          {summary.act ? `, under the ${summary.act}` : ""}
        </p>
        {tax ? (
          <>
            <h2 className="verdict__figure">Estimated tax {rupees(tax.total)}</h2>
            <TaxStrip tax={tax} />
            <p className="verdict__caveat">
              Includes 4% cess. Leaves out surcharge, the rebate for lower incomes, and losses carried forward from earlier years.
            </p>
          </>
        ) : (
          <h2 className="verdict__figure verdict__figure--muted">Gains calculated, tax rates not set up for this year</h2>
        )}
      </div>

      <div className="ledger">
        {hasEquity && (
          <section className="ledger__col ledger__col--ST">
            <h3>Short-term equity</h3>
            <p className="ledger__rule">Held 12 months or less{summary.sections ? `. ${summary.sections.equity_st}` : ""}</p>
            <Row label="Gains" value={rupees(eq.st_gains)} />
            <Row label="Losses" value={signedRupees(-Number(eq.st_losses))} />
            <Row label="Net after set-off" value={rupees(eq.net_st)} strong />
            {tax && <Row label={`Tax at ${pct(tax.rates.equity_st)}`} value={rupees(tax.equity_st)} strong />}
            {Number(eq.st_loss_carry_forward) > 0 && <Row label="Loss to carry forward" value={rupees(eq.st_loss_carry_forward)} muted />}
          </section>
        )}
        {hasEquity && (
          <section className="ledger__col ledger__col--LT">
            <h3>Long-term equity</h3>
            <p className="ledger__rule">Held over 12 months{summary.sections ? `. ${summary.sections.equity_lt}` : ""}</p>
            <Row label="Gains" value={rupees(eq.lt_gains)} />
            <Row label="Losses" value={signedRupees(-Number(eq.lt_losses))} />
            <Row label="Net after set-off" value={rupees(eq.net_lt_before_exemption)} />
            {tax && (
              <div className="meter" aria-label={`${rupees(exemptionUsed)} of the ${rupees(exemption)} exemption used`}>
                <div className="meter__track"><div className="meter__fill" style={{ width: `${Math.min(100, (exemptionUsed / exemption) * 100)}%` }} /></div>
                <span>{rupees(exemptionUsed)} of the {rupees(exemption)} yearly exemption used</span>
              </div>
            )}
            {tax && <Row label="Taxable" value={rupees(eq.taxable_lt)} strong />}
            {tax && <Row label={`Tax at ${pct(tax.rates.equity_lt)}`} value={rupees(tax.equity_lt)} strong />}
            {Number(eq.lt_loss_carry_forward) > 0 && <Row label="Loss to carry forward" value={rupees(eq.lt_loss_carry_forward)} muted />}
          </section>
        )}
        {hasCrypto && (
          <section className="ledger__col ledger__col--VDA">
            <h3>Crypto</h3>
            <p className="ledger__rule">Flat rate, no holding period{summary.sections ? `. ${summary.sections.vda}` : ""}</p>
            <Row label="Gains taxed" value={rupees(summary.vda.gains)} strong />
            <Row label="Losses" value={signedRupees(-Number(summary.vda.losses_not_set_off))} />
            <p className="ledger__note">Crypto losses can't reduce any gain, including other crypto gains, and can't be carried forward.</p>
            {tax && <Row label={`Tax at ${pct(tax.rates.vda)}`} value={rupees(tax.vda)} strong />}
          </section>
        )}
      </div>

      {warnings.length > 0 && (
        <section className="warnings" aria-label="Things to check">
          <h3>Check before you file</h3>
          <ul>
            {warnings.map((w, i) => <li key={i}>{w.message}</li>)}
          </ul>
        </section>
      )}

      <section className="sales">
        <div className="sales__head">
          <h3>Every sale, and the lots it came from</h3>
          <div className="sales__tools">
            {hasCrypto && hasEquity && (
              <div className="chips" role="group" aria-label="Filter sales">
                {[["all", "All"], ["equity", "Stocks and funds"], ["crypto", "Crypto"]].map(([k, l]) => (
                  <button key={k} className={filter === k ? "is-on" : ""} aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}</button>
                ))}
              </div>
            )}
            <button className="btn btn--ghost" onClick={() => download(`lotwise-${fy}.csv`, transfersToCsv(transfers, fy))}>Download CSV</button>
            <button className="btn btn--ghost" onClick={() => window.print()}>Print</button>
          </div>
        </div>
        <ul className="legend" aria-label="Colour key">
          <li><i className="swatch lot--LT" aria-hidden="true" />Long-term</li>
          <li><i className="swatch lot--ST" aria-hidden="true" />Short-term</li>
          <li><i className="swatch lot--VDA" aria-hidden="true" />Crypto</li>
        </ul>
        <div className="scroll-x">
          <table className="table">
            <thead>
              <tr>
                <th>Sold on</th><th>Instrument</th><th className="num">Quantity</th>
                <th className="num">Sale value</th><th className="num">Cost</th><th className="num">Gain</th>
                <th className="col-lots">Lots used</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((t) => (
                <tr key={t.id}>
                  <td className="nowrap">{day(t.sell_date)}</td>
                  <td>
                    <span className="instrument">{t.symbol}</span>
                    {t.isin && <small className="isin">{t.isin}</small>}
                  </td>
                  <td className="num">{qty(t.quantity)}</td>
                  <td className="num">{rupees(t.proceeds)}</td>
                  <td className="num">{rupees(t.cost)}</td>
                  <td className={`num ${Number(t.gain) < 0 ? "is-loss" : ""}`}>{signedRupees(t.gain)}</td>
                  <td className="col-lots"><LotStrip parts={t.parts} unmatched={t.unmatched_quantity} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {holdings.length > 0 && (
        <details className="holdings">
          <summary>Lots you still hold ({holdings.length})</summary>
          <div className="scroll-x">
            <table className="table table--compact">
              <thead><tr><th>Instrument</th><th>Bought on</th><th className="num">Quantity</th><th className="num">Cost</th></tr></thead>
              <tbody>
                {holdings.map((h, i) => (
                  <tr key={i}><td>{h.symbol}</td><td>{day(h.buy_date)}</td><td className="num">{qty(h.quantity)}</td><td className="num">{rupees(h.cost)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}
