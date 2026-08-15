import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "../store/auth";
import { AccountPage } from "./AccountPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listUsers: vi.fn().mockResolvedValue([]),
      listApiTokens: vi.fn().mockResolvedValue([]),
      listLlmCredentials: vi.fn().mockResolvedValue([]),
      createLlmCredential: vi.fn(),
      revokeLlmCredential: vi.fn(),
      runLiveDemo: vi.fn(),
    },
  };
});

const { api, ApiError } = await import("../api/client");

function renderAccountPage() {
  return render(
    <MemoryRouter>
      <AccountPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(api.listUsers).mockReset().mockResolvedValue([]);
  vi.mocked(api.listApiTokens).mockReset().mockResolvedValue([]);
  vi.mocked(api.listLlmCredentials).mockReset().mockResolvedValue([]);
  vi.mocked(api.createLlmCredential).mockReset();
  vi.mocked(api.revokeLlmCredential).mockReset();
  vi.mocked(api.runLiveDemo).mockReset();
  useAuthStore.setState({
    accessToken: "fake-token",
    refreshToken: "fake-refresh",
    role: "owner",
    userId: "user-1",
    orgId: "11111111-1111-1111-1111-111111111111",
  });
});

afterEach(() => {
  useAuthStore.getState().logout();
});

describe("AccountPage — LLM provider keys", () => {
  it("lists existing (non-revoked) credentials, masked to key_last4", async () => {
    vi.mocked(api.listLlmCredentials).mockResolvedValue([
      {
        id: "cred-1",
        provider: "openai",
        label: "personal key",
        key_last4: "3456",
        created_at: "2026-01-01T00:00:00Z",
        last_used_at: null,
        revoked_at: null,
      },
    ]);
    renderAccountPage();

    expect(await screen.findByText("personal key")).toBeInTheDocument();
    expect(screen.getByText("…3456")).toBeInTheDocument();
  });

  it("a revoked credential is filtered out of the active list", async () => {
    vi.mocked(api.listLlmCredentials).mockResolvedValue([
      {
        id: "cred-1",
        provider: "openai",
        label: "revoked key",
        key_last4: "0000",
        created_at: "2026-01-01T00:00:00Z",
        last_used_at: null,
        revoked_at: "2026-01-02T00:00:00Z",
      },
    ]);
    renderAccountPage();

    await waitFor(() => expect(api.listLlmCredentials).toHaveBeenCalled());
    expect(screen.queryByText("revoked key")).not.toBeInTheDocument();
    expect(screen.getByText("No LLM keys yet")).toBeInTheDocument();
  });

  it("adding a key calls createLlmCredential with the form's values and reloads the list", async () => {
    vi.mocked(api.createLlmCredential).mockResolvedValue({
      id: "cred-new",
      provider: "anthropic",
      label: "my key",
      key_last4: "9999",
      created_at: "2026-01-01T00:00:00Z",
      last_used_at: null,
      revoked_at: null,
    });
    const user = userEvent.setup();
    renderAccountPage();
    await waitFor(() => expect(api.listLlmCredentials).toHaveBeenCalledTimes(1));

    const llmSection = screen.getByText(/Bring your own OpenAI/).closest("section")!;
    await user.selectOptions(within(llmSection).getByRole("combobox"), "anthropic");
    await user.type(within(llmSection).getByPlaceholderText(/Label/), "my key");
    await user.type(within(llmSection).getByPlaceholderText("API key"), "sk-ant-secret");
    await user.click(within(llmSection).getByRole("button", { name: "Add key" }));

    await waitFor(() =>
      expect(api.createLlmCredential).toHaveBeenCalledWith("anthropic", "my key", "sk-ant-secret"),
    );
    // The list is reloaded (not just optimistically appended) after create.
    expect(api.listLlmCredentials).toHaveBeenCalledTimes(2);
  });

  it("revoking a key calls revokeLlmCredential with its id", async () => {
    vi.mocked(api.listLlmCredentials).mockResolvedValue([
      {
        id: "cred-1",
        provider: "openai",
        label: "personal key",
        key_last4: "3456",
        created_at: "2026-01-01T00:00:00Z",
        last_used_at: null,
        revoked_at: null,
      },
    ]);
    vi.mocked(api.revokeLlmCredential).mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderAccountPage();
    await screen.findByText("personal key");

    await user.click(screen.getByRole("button", { name: "Revoke" }));

    await waitFor(() => expect(api.revokeLlmCredential).toHaveBeenCalledWith("cred-1"));
  });
});

describe("AccountPage — live prompt-injection demo", () => {
  it("defaults to ollama and runs with no credential_id", async () => {
    vi.mocked(api.runLiveDemo).mockResolvedValue({
      trace_id: "trace-1",
      provider: "ollama",
      steps: [
        { tool_name: "tickets.read", args: {}, decision: "allowed", reason: null, result: {} },
        {
          tool_name: "payments.transfer",
          args: { amount: 500 },
          decision: "blocked",
          reason: "amount > 100",
          result: null,
        },
      ],
      final_text: "Could not complete the request.",
    });
    const user = userEvent.setup();
    renderAccountPage();
    await waitFor(() => expect(api.listLlmCredentials).toHaveBeenCalled());

    await user.click(screen.getByRole("button", { name: "Run live demo" }));

    await waitFor(() => expect(api.runLiveDemo).toHaveBeenCalledWith("ollama", null));
    expect(await screen.findByText(/blocked/)).toBeInTheDocument();
    expect(screen.getByText(/amount > 100/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View this trace in Incident Replay/ })).toHaveAttribute(
      "href",
      "/replay/trace-1",
    );
  });

  it("a cloud provider with no key added disables the run button", async () => {
    const user = userEvent.setup();
    renderAccountPage();
    await waitFor(() => expect(api.listLlmCredentials).toHaveBeenCalled());

    const demoSection = screen.getByText(/Run the live prompt-injection demo/).closest("section")!;
    const [providerSelect] = within(demoSection).getAllByRole("combobox") as [HTMLElement];
    await user.selectOptions(providerSelect, "openai");

    expect(within(demoSection).getByRole("button", { name: "Run live demo" })).toBeDisabled();
  });

  it("surfaces a structured error message instead of a silent failure", async () => {
    vi.mocked(api.runLiveDemo).mockRejectedValue(
      new ApiError(429, "LLM_RATE_LIMITED", "rate-limited by provider"),
    );
    const user = userEvent.setup();
    renderAccountPage();
    await waitFor(() => expect(api.listLlmCredentials).toHaveBeenCalled());

    await user.click(screen.getByRole("button", { name: "Run live demo" }));

    expect(await screen.findByText("rate-limited by provider")).toBeInTheDocument();
  });
});
