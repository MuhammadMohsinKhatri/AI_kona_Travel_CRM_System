import { useEffect, useState } from "react";
import { FleetEtaResult, FleetStatus, api } from "../api/client";
import { Empty, Loading } from "../components/ui";

/** Where the trucks are, how much fuel they have, and who is on the clock —
 *  the live-status view. Chat answers the same questions one at a time; this
 *  page exists for "how's everything looking right now" in one glance.
 *
 *  The two sections fail independently on purpose. A dead Samsara token
 *  should never blank out whether anyone is clocked in, and the reverse —
 *  each section says plainly when it has nothing to show and why, rather
 *  than the whole page going empty over one missing credential. */
export default function Fleet() {
  const [data, setData] = useState<FleetStatus | null>(null);
  const [error, setError] = useState("");

  function load() {
    api.fleetStatus().then(setData).catch((e: any) =>
      setError(e?.message || "Couldn't load fleet status")
    );
  }
  useEffect(() => {
    load();
    // Live-ish without being chatty — a truck's position or a clock event is
    // worth seeing within a minute of it happening, not on every render.
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);

  return (
    <>
      <h1 className="page-title">Fleet &amp; Staff</h1>
      <p className="page-sub">
        Live from Samsara and Square — where each truck is, how much fuel it
        has, and who's clocked in right now. Low fuel and clock events also
        push to Telegram and land on{" "}
        <a href="/alerts">Needs Attention</a>.
      </p>

      {error && <p style={{ color: "var(--crit)" }}>{error}</p>}
      {!data ? (
        <Loading />
      ) : (
        <>
          {data.trucks.ok && data.trucks.trucks.length > 0 && (
            <EtaWidget trucks={data.trucks.trucks.map((t) => t.name)} />
          )}
          <div className="section-title" style={{ marginTop: 4 }}>Trucks</div>
          {!data.trucks.ok ? (
            <p className="muted">{data.trucks.error}</p>
          ) : data.trucks.trucks.length === 0 ? (
            <Empty text="No trucks on the Samsara account." />
          ) : (
            <div className="grid cols-3" style={{ marginBottom: 20 }}>
              {data.trucks.trucks.map((t) => (
                <div
                  key={t.id}
                  className="card"
                  style={{
                    borderLeft: `3px solid ${t.low_fuel ? "var(--warn)" : "var(--ok)"}`,
                  }}
                >
                  <div style={{ fontWeight: 700 }}>{t.name}</div>
                  <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>
                    {t.address || "Position not reported"}
                  </div>
                  <div className="flex between" style={{ marginTop: 10 }}>
                    <span
                      style={{
                        fontWeight: 700,
                        color: t.low_fuel ? "var(--warn)" : undefined,
                      }}
                    >
                      {t.fuel_percent == null ? "No fuel reading" : `${Math.round(t.fuel_percent)}% fuel`}
                    </span>
                    {t.low_fuel && <span className="badge amber">low</span>}
                  </div>
                  {t.position_at && (
                    <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
                      Updated {new Date(t.position_at).toLocaleTimeString()}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="section-title">On the clock</div>
          {!data.staff.ok ? (
            <p className="muted">{data.staff.error}</p>
          ) : data.staff.on_the_clock.length === 0 ? (
            <Empty text="Nobody is currently clocked in." />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Name</th><th>Brand</th><th>Clocked in</th>
                  </tr>
                </thead>
                <tbody>
                  {data.staff.on_the_clock.map((s, i) => (
                    <tr key={i}>
                      <td>{s.name}</td>
                      <td className="muted">{s.brand}</td>
                      <td>{new Date(s.clock_in).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </>
  );
}

/** How long a truck needs to reach somewhere, from where it is right now.
 *
 *  The one travel tool that earns a permanent spot on this page rather than
 *  staying chat-only: it's anchored to a truck that's already listed above,
 *  so picking one from a dropdown is less friction than typing its name.
 *  Multi-stop routes, a plain point-to-point time and Street View stay in
 *  Aimee — each needs fresh input every time regardless of surface, so a page
 *  for them would just be this same form with none of the conversation. */
function EtaWidget({ trucks }: { trucks: string[] }) {
  const [truck, setTruck] = useState(trucks[0] || "");
  const [destination, setDestination] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<FleetEtaResult | null>(null);

  async function go() {
    if (!truck || !destination.trim()) return;
    setBusy(true);
    setResult(null);
    try {
      setResult(await api.fleetEta(truck, destination.trim()));
    } catch (e) {
      setResult({ ok: false, error: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <div className="section-title" style={{ marginTop: 0 }}>ETA for a truck</div>
      <div className="flex" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <select
          className="select"
          value={truck}
          onChange={(e) => setTruck(e.target.value)}
        >
          {trucks.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <input
          className="input"
          style={{ flex: "1 1 220px" }}
          placeholder="Destination address"
          value={destination}
          onKeyDown={(e) => e.key === "Enter" && go()}
          onChange={(e) => setDestination(e.target.value)}
        />
        <button className="btn primary" disabled={busy || !destination.trim()} onClick={go}>
          {busy ? "Working it out…" : "Get ETA"}
        </button>
      </div>

      {result && (
        result.ok ? (
          <p style={{ marginTop: 10, marginBottom: 0 }}>
            <strong>{result.truck}</strong> is {result.distance} from{" "}
            {result.destination} — about <strong>{result.duration}</strong>
            {result.traffic_aware ? " in current traffic" : ""}.
          </p>
        ) : (
          <p style={{ marginTop: 10, marginBottom: 0, color: "var(--crit)" }}>
            {result.error}
          </p>
        )
      )}
    </div>
  );
}
