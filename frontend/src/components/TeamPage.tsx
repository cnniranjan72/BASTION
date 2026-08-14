import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "../api/client";
import { useAuthStore } from "../store/auth";
import { toast } from "../store/toast";
import { TableSkeleton } from "./TableSkeleton";
import { TopBar } from "./TopBar";
import type { TeamMember, UserRole } from "../api/types";

const ROLES: UserRole[] = ["owner", "admin", "approver", "viewer"];

function canManage(role: string | null): boolean {
  return role === "owner" || role === "admin";
}

export function TeamPage() {
  const role = useAuthStore((s) => s.role);
  const currentUserId = useAuthStore((s) => s.userId);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [newRole, setNewRole] = useState<UserRole>("viewer");
  const [creating, setCreating] = useState(false);
  const [reveal, setReveal] = useState<{ email: string; password: string } | null>(null);
  const [copied, setCopied] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setMembers(await api.listUsers());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load team");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setCreating(true);
    try {
      const created = await api.createUser(email, newRole);
      setReveal({ email: created.email, password: created.temporary_password });
      setCopied(false);
      setEmail("");
      setNewRole("viewer");
      toast.success(`${created.email} added as ${created.role}`);
      await load();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to add teammate";
      setError(message);
      toast.error(message);
    } finally {
      setCreating(false);
    }
  }

  async function handleRoleChange(userId: string, role: UserRole) {
    setError(null);
    try {
      await api.updateUserRole(userId, role);
      toast.success("Role updated");
      await load();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Failed to update role";
      setError(message);
      toast.error(message);
      await load(); // revert the optimistic-looking <select> back to the real value
    }
  }

  async function copyPassword() {
    if (!reveal) return;
    await navigator.clipboard.writeText(reveal.password);
    setCopied(true);
  }

  return (
    <div className="dashboard">
      <TopBar liveStatus={null} />
      <div className="page">
        <div className="page__header">
          <h1>Team</h1>
          <p className="page__subtitle">
            Four roles: owner and admin manage agents/policies/team; approver resolves pending
            approvals; viewer sees everything, changes nothing.
          </p>
        </div>

        {error && <p className="page__error">{error}</p>}

        {reveal && (
          <div className="key-reveal">
            <p>
              <strong>{reveal.email}</strong> added. Share this temporary password out of band —
              it won't be shown again.
            </p>
            <div className="key-reveal__row">
              <code>{reveal.password}</code>
              <button onClick={copyPassword}>{copied ? "Copied" : "Copy"}</button>
            </div>
            <button className="key-reveal__dismiss" onClick={() => setReveal(null)}>
              Done
            </button>
          </div>
        )}

        {canManage(role) && (
          <form className="inline-form" onSubmit={handleCreate}>
            <input
              type="email"
              placeholder="teammate@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
            <select value={newRole} onChange={(e) => setNewRole(e.target.value as UserRole)}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <button type="submit" disabled={creating}>
              {creating ? "Adding…" : "Add teammate"}
            </button>
          </form>
        )}

        {loading ? (
          <TableSkeleton />
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Joined</th>
                  {canManage(role) && <th>Change role</th>}
                </tr>
              </thead>
              <tbody>
                {members.map((member) => (
                  <tr key={member.id}>
                    <td>
                      {member.email}
                      {member.id === currentUserId && (
                        <span className="badge" style={{ marginLeft: "0.5rem" }}>
                          you
                        </span>
                      )}
                    </td>
                    <td className="data-table__mono">{member.role}</td>
                    <td>{new Date(member.created_at).toLocaleString()}</td>
                    {canManage(role) && (
                      <td>
                        <select
                          value={member.role}
                          onChange={(e) =>
                            handleRoleChange(member.id, e.target.value as UserRole)
                          }
                        >
                          {ROLES.map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                      </td>
                    )}
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
