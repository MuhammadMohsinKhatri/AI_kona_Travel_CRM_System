import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, Invoice, Page } from "../api/client";
import { Badge, DeleteButton, Empty, Loading, money } from "../components/ui";

export default function Invoices() {
  const [data, setData] = useState<Page<Invoice> | null>(null);
  const [onlyVariance, setOnlyVariance] = useState(false);
  const navigate = useNavigate();

  const reload = () =>
    api.invoices(onlyVariance ? { has_variance: "true" } : {}).then(setData);

  useEffect(() => {
    setData(null);
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onlyVariance]);

  return (
    <>
      <h1 className="page-title">Invoices</h1>
      <p className="page-sub">Invoice drafts created in the CRM by the pipeline.</p>

      <div className="toolbar">
        <label className="flex" style={{ gap: 6 }}>
          <input
            type="checkbox"
            checked={onlyVariance}
            onChange={(e) => setOnlyVariance(e.target.checked)}
          />
          Only with variance
        </label>
        {data && <span className="muted">{data.total} invoices</span>}
      </div>

      {!data ? (
        <Loading />
      ) : data.items.length === 0 ? (
        <Empty text="No invoices yet." />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Invoice</th>
                <th>Type</th>
                <th>Status</th>
                <th className="right">Subtotal</th>
                <th className="right">Tax</th>
                <th className="right">Total</th>
                <th className="right">Variance</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((inv) => (
                <tr key={inv.id} onClick={() => navigate(`/events/${inv.event_id}`)}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{inv.title}</div>
                    <div className="muted" style={{ fontSize: 12 }}>{inv.invoice_number}</div>
                  </td>
                  <td>{inv.invoice_type}</td>
                  <td><Badge kind={inv.status}>{inv.status}</Badge></td>
                  <td className="right">{money(inv.subtotal)}</td>
                  <td className="right">{money(inv.tax_amount)}</td>
                  <td className="right"><strong>{money(inv.grand_total)}</strong></td>
                  <td className="right">
                    {inv.has_variance ? (
                      <span style={{ color: "var(--warn)" }}>{money(inv.variance_amount)}</span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td className="actions">
                    <DeleteButton
                      title="Delete this invoice record (KonaOS is not touched)"
                      onDelete={async () => { await api.deleteInvoice(inv.id); await reload(); }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
