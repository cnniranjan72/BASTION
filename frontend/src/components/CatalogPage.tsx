import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { EmptyBoxIcon } from "./icons";
import { EmptyState } from "./EmptyState";
import { TableSkeleton } from "./TableSkeleton";
import { TopBar } from "./TopBar";
import type { CatalogItem } from "../api/types";

// Track 01: the agent-readable merchant catalog (catalog/src/bastion_catalog)
// that the buyer demo and razorpay.purchase itself read from -- previously
// visible only in terminal output or a trace's raw args, never as its own
// page. One fetch, real data, same table pattern AgentsPage/TracesPage
// already use -- no cart, no interactivity beyond what exists elsewhere.
export function CatalogPage() {
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listCatalog()
      .then(setItems)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load catalog");
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page">
        <div className="page__header">
          <h1>Catalog</h1>
          <p className="page__subtitle">
            The agent-readable merchant catalog — the same real data the AI buyer agent browses
            and <code>razorpay.purchase</code> reads from, not a mockup product list.
          </p>
        </div>

        {error && <p className="page__error">{error}</p>}

        {loading ? (
          <TableSkeleton />
        ) : items.length === 0 ? (
          <EmptyState icon={EmptyBoxIcon} title="Catalog is empty">
            The catalog service has no items seeded yet.
          </EmptyState>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>SKU</th>
                  <th>Name</th>
                  <th>Description</th>
                  <th>Price</th>
                  <th>Stock</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.sku}>
                    <td className="data-table__mono">{item.sku}</td>
                    <td>{item.name}</td>
                    <td>{item.description}</td>
                    <td>₹{item.price_inr.toLocaleString("en-IN")}</td>
                    <td>{item.stock}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
