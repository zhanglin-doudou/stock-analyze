import { describe, expect, it } from "vitest";
import { displayValue, signTone } from "./format";

describe("signTone（只判符号，不解析数值）", () => {
  it("正/负/零/空", () => {
    expect(signTone("620.00")).toBe("pos");
    expect(signTone("+0.36%")).toBe("pos");
    expect(signTone("-500.00")).toBe("neg");
    expect(signTone("-0.00%")).toBe("flat");
    expect(signTone("0.00")).toBe("flat");
    expect(signTone(null)).toBe("none");
    expect(signTone("—")).toBe("none");
    expect(signTone("")).toBe("none");
  });
});

describe("displayValue", () => {
  it("空值统一占位", () => {
    expect(displayValue("170620.00")).toBe("170620.00");
    expect(displayValue(null)).toBe("—");
    expect(displayValue("", "暂无")).toBe("暂无");
  });
});
