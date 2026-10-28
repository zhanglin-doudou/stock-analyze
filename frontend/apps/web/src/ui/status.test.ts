import { describe, expect, it } from "vitest";
import {
  buildStateLabel,
  buildStateTone,
  dataStatusLabel,
  dataStatusTone,
  isValueVisible,
  needsRebuild,
  worstDataStatus,
} from "./status";

describe("status 语义", () => {
  it("两轴标签", () => {
    expect(buildStateLabel("current")).toBe("最新");
    expect(buildStateLabel("missing")).toBe("未构建");
    expect(dataStatusLabel("blocked")).toBe("阻断");
    expect(dataStatusLabel("pending_window")).toBe("窗口未满");
    expect(dataStatusLabel("unknown")).toBe("unknown");
  });

  it("色调映射", () => {
    expect(buildStateTone("current")).toBe("ok");
    expect(buildStateTone("stale")).toBe("warn");
    expect(buildStateTone("blocked")).toBe("danger");
    expect(dataStatusTone("ok")).toBe("ok");
    expect(dataStatusTone("degraded")).toBe("warn");
    expect(dataStatusTone("missing")).toBe("muted");
    expect(dataStatusTone("pending_window")).toBe("pending");
  });

  it("页面状态取最严重", () => {
    expect(worstDataStatus(["ok", "degraded"])).toBe("degraded");
    expect(worstDataStatus(["degraded", "blocked", "insufficient"])).toBe("blocked");
    expect(worstDataStatus(["stale", "missing"])).toBe("missing");
    expect(worstDataStatus([])).toBe("ok");
    expect(worstDataStatus(["bogus"])).toBe("ok");
  });

  it("blocked/missing 不展示数值", () => {
    expect(isValueVisible("ok")).toBe(true);
    expect(isValueVisible("degraded")).toBe(true);
    expect(isValueVisible("blocked")).toBe(false);
    expect(isValueVisible("missing")).toBe(false);
  });

  it("构建状态决定是否提示重建", () => {
    expect(needsRebuild("current")).toBe(false);
    expect(needsRebuild("stale")).toBe(true);
    expect(needsRebuild("missing")).toBe(true);
    expect(needsRebuild("blocked")).toBe(true);
  });
});
