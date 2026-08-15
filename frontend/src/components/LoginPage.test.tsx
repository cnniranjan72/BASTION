import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { useAuthStore } from "../store/auth";
import { LoginPage } from "./LoginPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, api: { ...actual.api, login: vi.fn() } };
});

const { api } = await import("../api/client");
const loginMock = vi.mocked(api.login);

function renderLoginPage() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<div>HOME PAGE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  loginMock.mockReset();
  // useAuthStore is a real, module-level singleton — a prior test's
  // setTokens() call otherwise survives into the next test even after
  // localStorage.clear(), since that only affects what a *fresh* module
  // load would rehydrate, not the already-live in-memory state.
  useAuthStore.setState({
    accessToken: null,
    refreshToken: null,
    role: null,
    userId: null,
    orgId: null,
  });
});

afterEach(() => {
  localStorage.clear();
});

describe("LoginPage", () => {
  it("on success, stores the tokens and navigates to /", async () => {
    loginMock.mockResolvedValueOnce({
      access_token: "at",
      refresh_token: "rt",
      token_type: "bearer",
      role: "owner",
    });
    const user = userEvent.setup();
    renderLoginPage();

    await user.type(screen.getByLabelText("Email"), "demo@bastion.dev");
    await user.type(screen.getByLabelText("Password"), "correct horse battery staple");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(screen.getByText("HOME PAGE")).toBeInTheDocument());
    expect(useAuthStore.getState().accessToken).toBe("at");
  });

  it("on an ApiError, shows the server's message and stays on the page", async () => {
    loginMock.mockRejectedValueOnce(new ApiError(401, "INVALID_CREDENTIALS", "wrong password"));
    const user = userEvent.setup();
    renderLoginPage();

    await user.type(screen.getByLabelText("Email"), "demo@bastion.dev");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("wrong password")).toBeInTheDocument();
    expect(screen.queryByText("HOME PAGE")).not.toBeInTheDocument();
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("on a non-ApiError failure, shows a generic fallback message", async () => {
    loginMock.mockRejectedValueOnce(new TypeError("network down"));
    const user = userEvent.setup();
    renderLoginPage();

    await user.type(screen.getByLabelText("Email"), "demo@bastion.dev");
    await user.type(screen.getByLabelText("Password"), "whatever");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("Login failed")).toBeInTheDocument();
  });

  it("disables the submit button while the request is in flight", async () => {
    let resolveLogin!: (value: {
      access_token: string;
      refresh_token: string;
      token_type: "bearer";
      role: "owner";
    }) => void;
    loginMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveLogin = resolve;
      }),
    );
    const user = userEvent.setup();
    renderLoginPage();

    await user.type(screen.getByLabelText("Email"), "demo@bastion.dev");
    await user.type(screen.getByLabelText("Password"), "correct horse battery staple");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(screen.getByRole("button", { name: /signing in/i })).toBeDisabled();

    resolveLogin({ access_token: "at", refresh_token: "rt", token_type: "bearer", role: "owner" });
    await waitFor(() => expect(screen.getByText("HOME PAGE")).toBeInTheDocument());
  });
});
