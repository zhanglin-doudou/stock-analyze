import { describe, expect, it } from "vitest";

import { signedClass } from "./signed";

describe("signedClass", () => {
  it("正数着涨色、负数着跌色", () => {
    expect(signedClass("1.23")).toBe("money money-pos");
    expect(signedClass("1.23%")).toBe("money money-pos");
    expect(signedClass("-1.23")).toBe("money money-neg");
    expect(signedClass("-1.23%")).toBe("money money-neg");
  });

  it("零值（含百分号与负零）保持中性", () => {
    for (const value of ["0", "0.00", "0.0000", "0.00%", "-0.00", "+0.00"]) {
      expect(signedClass(value)).toBe("money");
    }
  });

  it("空值与占位符保持中性", () => {
    expect(signedClass(null)).toBe("money");
    expect(signedClass(undefined)).toBe("money");
    expect(signedClass("")).toBe("money");
    expect(signedClass("—")).toBe("money");
  });
});
