import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { rich } from "./rich";

afterEach(cleanup);

function html(text: string): string {
  return render(<p>{rich(text)}</p>).container.firstElementChild?.innerHTML ?? "";
}

describe("rich", () => {
  it("renders each **bold** run as strong and keeps the rest as typed", () => {
    expect(html("Due 18 Nov. **It hasn't been paid yet.** It waits for **you**."))
      .toBe("Due 18 Nov. <strong>It hasn't been paid yet.</strong> It waits for <strong>you</strong>.");
  });

  it("leaves an unclosed or spaced ** as typed", () => {
    expect(html("**still streaming")).toBe("**still streaming");
    expect(html("2 ** 3 and 4 ** 5")).toBe("2 ** 3 and 4 ** 5");
  });

  it("keeps a bold run on one line, so a stray ** never pairs across paragraphs", () => {
    expect(html("2 **3\n\nNext **bold**")).toBe("2 **3\n\nNext <strong>bold</strong>");
    expect(html("**x**")).toBe("<strong>x</strong>");
  });

  it("never renders the text as HTML", () => {
    expect(html("**<img src=x>**")).toBe("<strong>&lt;img src=x&gt;</strong>");
  });
});
