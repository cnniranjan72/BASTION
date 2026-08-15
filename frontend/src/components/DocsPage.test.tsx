import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { DocsPage } from "./DocsPage";

// DocsPage renders the full TopBar too, which has its own nav links to
// /agents and /account — scope queries to .docs-content so a match there
// (the page's own inline prose links, the thing actually under test) can't
// collide with an identically-labeled nav link.
function docsContent(): HTMLElement {
  return document.querySelector(".docs-content") as HTMLElement;
}

describe("DocsPage", () => {
  it("renders every TOC section as a real, findable section heading", () => {
    render(
      <MemoryRouter>
        <DocsPage />
      </MemoryRouter>,
    );

    const sections = [
      "Overview",
      "Quickstart: wire up an agent",
      "Local development",
      "Bring your own LLM key",
      "API reference",
      "Deployment",
      "Architecture, briefly",
    ];
    for (const heading of sections) {
      expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
    }
  });

  it("links into the real /account and /agents routes, not dead hrefs", () => {
    render(
      <MemoryRouter>
        <DocsPage />
      </MemoryRouter>,
    );

    const content = within(docsContent());
    expect(content.getAllByRole("link", { name: "Account" })[0]).toHaveAttribute(
      "href",
      "/account",
    );
    expect(content.getByRole("link", { name: "Agents" })).toHaveAttribute("href", "/agents");
  });
});
