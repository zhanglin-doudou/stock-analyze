import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ImportBatch, JobAccepted, JobEvent, Preview } from "@sa/api-client";
import { Import } from "./Import";
import { api } from "../client";

vi.mock("../client", () => ({
  api: {
    imports: {
      preview: vi.fn(),
      saveMapping: vi.fn(),
      saveTypes: vi.fn(),
      getPreview: vi.fn(),
      commit: vi.fn(),
      getBatch: vi.fn(),
      revoke: vi.fn(),
    },
    jobs: {
      events: vi.fn(async function* (): AsyncGenerator<JobEvent, void, void> {
        yield {
          id: "1",
          event: "done",
          data: { job_id: "job_1", status: "succeeded", result_ref: null },
        };
      }),
    },
  },
}));

const preview = vi.mocked(api.imports.preview);
const saveMapping = vi.mocked(api.imports.saveMapping);
const saveTypes = vi.mocked(api.imports.saveTypes);
const commit = vi.mocked(api.imports.commit);
const getBatch = vi.mocked(api.imports.getBatch);

const CATALOG = [
  { field: "trade_date", label: "成交日期", level: "L1", mergeable: false },
  { field: "security_code", label: "证券代码", level: "L1-trade", mergeable: false },
  { field: "quantity", label: "成交数量", level: "L1-trade", mergeable: false },
  { field: "price", label: "成交价格", level: "L1-trade", mergeable: false },
  { field: "amount", label: "发生金额", level: "L2", mergeable: false },
  { field: "side", label: "买卖标志", level: "L2", mergeable: false },
  { field: "summary", label: "摘要", level: "L2", mergeable: false },
  { field: "other_fee", label: "其他费用", level: "L2", mergeable: true },
  { field: "note", label: "备注", level: "L3", mergeable: false },
];

const CATEGORIES = [
  { code: "buy", label: "买入" },
  { code: "sell", label: "卖出" },
  { code: "dividend", label: "分红" },
];

function basePreview(overrides: Partial<Preview> = {}): Preview {
  return {
    preview_id: "prv_1",
    file: { name: "huatai.xlsx", sha256: "b".repeat(64), bytes: 2048 },
    columns: [],
    date_format: "YYYYMMDD",
    warnings: [],
    estimated: {
      parsed: 0,
      inserted: 0,
      skipped: 0,
      quarantined: 0,
      discarded: 0,
      estimated_available: false,
    },
    cash_balance_available: true,
    can_commit: false,
    expires_at: "2026-11-13T01:00:00Z",
    broker: "huatai",
    stage: "mapping",
    discarded_count: 0,
    mapping_version: 1,
    requires_reimport_ack: false,
    catalog: CATALOG,
    categories: CATEGORIES,
    mapping_items: [],
    ignored_columns: [],
    ...overrides,
  };
}

function mappingPreview(withUnmapped = false): Preview {
  const columns: Preview["columns"] = [
    {
      index: 0,
      key: "k1",
      name: "成交日期",
      sample: ["20260304"],
      samples: ["20260304"],
      suggested_field: "trade_date",
      suggested_options: {},
      level: "L1",
      confidence: 1,
    },
    {
      index: 1,
      key: "k2",
      name: "证券代码",
      sample: ["600519"],
      samples: ["600519"],
      suggested_field: "security_code",
      suggested_options: {},
      level: "L1-trade",
      confidence: 1,
    },
    {
      index: 2,
      key: "k3",
      name: "发生金额",
      sample: ["-1000.00"],
      samples: ["-1000.00"],
      suggested_field: "amount",
      suggested_options: { amount_signed: true },
      level: "L2",
      confidence: 0.9,
    },
    {
      index: 3,
      key: "k4",
      name: "买卖标志",
      sample: ["买入"],
      samples: ["买入"],
      suggested_field: "side",
      suggested_options: {},
      level: "L2",
      confidence: 1,
    },
  ];
  if (withUnmapped) {
    columns.push({
      index: 4,
      key: "k5",
      name: "备注说明",
      sample: ["转托管"],
      samples: ["转托管"],
      suggested_field: null,
      suggested_options: {},
      level: null,
      confidence: 0,
    });
  }
  return basePreview({ columns });
}

function typesPreview(overrides: Partial<Preview> = {}): Preview {
  const columns = mappingPreview().columns;
  return basePreview({
    stage: "types",
    columns,
    mapping_items: columns.map((column) => ({
      column_key: column.key ?? "",
      field: column.suggested_field ?? "",
      options: {},
    })),
    types: [
      {
        type_key: "证券买入",
        type_label: "证券买入",
        row_count: 3,
        amount_sum: "-3000.00",
        samples: ["证券买入"],
        suggested_category: "buy",
        category: null,
        needs_confirmation: true,
        discard: false,
      },
    ],
    ...overrides,
  });
}

function readyPreview(overrides: Partial<Preview> = {}): Preview {
  return basePreview({
    stage: "ready",
    can_commit: true,
    estimated: {
      parsed: 3,
      inserted: 3,
      skipped: 0,
      quarantined: 0,
      discarded: 0,
      estimated_available: true,
    },
    ...overrides,
  });
}

const acceptedJob: JobAccepted = {
  job_id: "job_1",
  type: "import_commit",
  status: "pending",
  batch_id: "bat_1",
  links: { self: "/api/v1/jobs/job_1", events: "/api/v1/jobs/job_1/events" },
};

const batch: ImportBatch = {
  batch_id: "bat_1",
  status: "succeeded",
  mode: "full",
  broker: "huatai",
  file_name: "huatai.xlsx",
  counts: { parsed: 3, inserted: 3, skipped: 0, quarantined: 0, discarded: 0 },
  filled_count: 0,
  warnings: [],
  assumptions: {},
  created_at: "2026-11-13T00:00:00Z",
  finished_at: "2026-11-13T00:00:05Z",
  revoked_at: null,
  can_revoke: true,
  mapping_source: "manual",
};

function renderPage(): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={["/import"]}>
      <Import />
    </MemoryRouter>,
  );
}

async function upload(): Promise<void> {
  const input = document.querySelector('input[type="file"]');
  if (!(input instanceof HTMLInputElement)) throw new Error("找不到文件输入框");
  fireEvent.change(input, { target: { files: [new File(["x"], "huatai.xlsx")] } });
}

beforeEach(() => {
  vi.clearAllMocks();
  preview.mockResolvedValue(mappingPreview());
  saveMapping.mockResolvedValue(typesPreview());
  saveTypes.mockResolvedValue(readyPreview());
  commit.mockResolvedValue(acceptedJob);
  getBatch.mockResolvedValue(batch);
});

afterEach(cleanup);

describe("Import 向导（v2 两阶段确认）", () => {
  it("上传后进入列对应；接受系统建议后保存映射并进入类型步骤", async () => {
    renderPage();
    await upload();

    expect(await screen.findByText("2 · 列对应")).toBeTruthy();
    const next = screen.getByRole("button", { name: "继续" }) as HTMLButtonElement;
    expect(next.disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "全部接受系统建议" }));
    await waitFor(() => expect(next.disabled).toBe(false));
    fireEvent.click(next);

    await waitFor(() => expect(saveMapping).toHaveBeenCalledTimes(1));
    const [previewId, body, ifMatch] = saveMapping.mock.calls[0];
    expect(previewId).toBe("prv_1");
    expect(ifMatch).toBe("1");
    expect(body.acknowledge_unmapped).toBe(false);
    expect(body.items?.map((item) => item.target_field)).toEqual([
      "trade_date",
      "security_code",
      "amount",
      "side",
    ]);
    expect(body.items?.[2].options).toEqual({ amount_signed: true });
    expect(await screen.findByText("3 · 账单类型")).toBeTruthy();
  });

  it("账单类型步骤提供默认收起的 Category 说明，展开后可读全部 11 类与真实摘要案例", async () => {
    renderPage();
    await upload();

    await screen.findByText("2 · 列对应");
    fireEvent.click(screen.getByRole("button", { name: "全部接受系统建议" }));
    const next = screen.getByRole("button", { name: "继续" }) as HTMLButtonElement;
    await waitFor(() => expect(next.disabled).toBe(false));
    fireEvent.click(next);
    await screen.findByText("3 · 账单类型");

    const toggle = screen.getByRole("button", { name: /Category 说明/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("几个常见例子")).toBeNull();

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("几个常见例子")).toBeTruthy();
    expect(screen.getByText("「市值认购资金扣收」")).toBeTruthy();
    for (const name of [
      "买入",
      "卖出",
      "送转",
      "分红",
      "配股",
      "利息",
      "费用",
      "资金划转",
      "新股/可转债中签",
      "券商行为",
      "其他",
    ]) {
      expect(screen.getAllByText(name).length).toBeGreaterThan(0);
    }

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("几个常见例子")).toBeNull();
  });

  it("存在未确认列时先弹确认框，确认后带 acknowledge_unmapped=true 保存", async () => {
    preview.mockResolvedValue(mappingPreview(true));
    renderPage();
    await upload();
    await screen.findByText("2 · 列对应");
    fireEvent.click(screen.getByRole("button", { name: "全部接受系统建议" }));
    // 「备注说明」没有系统建议，全部接受后仍是未确认列。

    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    expect(await screen.findByText("这些列不会导入")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "确认继续" }));

    await waitFor(() => expect(saveMapping).toHaveBeenCalledTimes(1));
    expect(saveMapping.mock.calls[0][1].acknowledge_unmapped).toBe(true);
    expect(saveMapping.mock.calls[0][1].ignored).toEqual([]);
  });

  it("类型确认后进入确认导入；重导风险需勾选确认才可提交", async () => {
    saveTypes.mockResolvedValue(readyPreview({ requires_reimport_ack: true }));
    renderPage();
    await upload();
    await screen.findByText("2 · 列对应");
    fireEvent.click(screen.getByRole("button", { name: "全部接受系统建议" }));
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    await screen.findByText("3 · 账单类型");

    // E：系统建议（buy）不自动跳过，也不提供逐项确认，必须整步点确认。
    expect(screen.queryByRole("switch", { name: "确认 证券买入" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "确认这些类型并继续" }));
    await waitFor(() => expect(saveTypes).toHaveBeenCalledTimes(1));
    expect(saveTypes.mock.calls[0][1].resolutions).toEqual([
      { type_key: "证券买入", category: "buy", discard: false },
    ]);

    const submit = (await screen.findByRole("button", { name: "确认导入" })) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.click(screen.getByRole("switch", { name: "确认重新导入" }));
    await waitFor(() => expect(submit.disabled).toBe(false));
    fireEvent.click(submit);

    await waitFor(() =>
      expect(commit).toHaveBeenCalledWith(
        { preview_id: "prv_1", mode: "full", acknowledge_reimport: true },
        expect.any(String),
      ),
    );
    expect(await screen.findByText(/插入 3 行/)).toBeTruthy();
  });

  it("确认步可退回账单类型重新调整：复用同一预览，不重传也不提交", async () => {
    saveTypes.mockResolvedValue(readyPreview({ types: typesPreview().types }));
    renderPage();
    await upload();
    await screen.findByText("2 · 列对应");
    fireEvent.click(screen.getByRole("button", { name: "全部接受系统建议" }));
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    await screen.findByText("3 · 账单类型");
    fireEvent.click(screen.getByRole("button", { name: "确认这些类型并继续" }));
    await screen.findByText("4 · 确认导入");

    fireEvent.click(screen.getByRole("button", { name: "修改账单类型" }));

    expect(await screen.findByText("3 · 账单类型")).toBeTruthy();
    expect(preview).toHaveBeenCalledTimes(1);
    expect(commit).not.toHaveBeenCalled();
  });

  it("有隔离行时先弹窗列出全部行与原因，确认后仍可提交", async () => {
    saveTypes.mockResolvedValue(
      readyPreview({
        estimated: {
          parsed: 1,
          inserted: 1,
          skipped: 0,
          quarantined: 2,
          discarded: 0,
          estimated_available: true,
        },
        quarantine_rows: [
          {
            line_no: 3,
            reason_code: "DUPLICATE_IN_FILE",
            reason: "文件内重复行",
            date: "2026-04-01",
            security_code: "600001",
            summary: "证券买入",
            amount: "1000.00",
          },
          {
            line_no: 4,
            reason_code: "INVALID_ROW",
            reason: "字段无法解析（日期/数量/价格等）",
            date: "2026-04-32",
            security_code: "600009",
            summary: "证券买入",
            amount: "1000.00",
          },
        ],
      }),
    );
    renderPage();
    await upload();
    await screen.findByText("2 · 列对应");
    fireEvent.click(screen.getByRole("button", { name: "全部接受系统建议" }));
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    await screen.findByText("3 · 账单类型");
    fireEvent.click(screen.getByRole("button", { name: "确认这些类型并继续" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("有 2 行不会导入")).toBeTruthy();
    expect(within(dialog).getByText("3")).toBeTruthy();
    expect(within(dialog).getByText("4")).toBeTruthy();
    expect(within(dialog).getByText("2026-04-01")).toBeTruthy();
    expect(within(dialog).getByText("2026-04-32")).toBeTruthy();
    expect(within(dialog).getByText("文件内重复行")).toBeTruthy();
    expect(within(dialog).getByText("字段无法解析（日期/数量/价格等）")).toBeTruthy();
    expect(commit).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "知悉，继续导入" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(screen.getByText("4 · 确认导入")).toBeTruthy();
    const submit = screen.getByRole("button", { name: "确认导入" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);
    await waitFor(() => expect(commit).toHaveBeenCalledTimes(1));
  });

  it("没有隔离行时不出现隔离确认这一步", async () => {
    renderPage();
    await upload();
    await screen.findByText("2 · 列对应");
    fireEvent.click(screen.getByRole("button", { name: "全部接受系统建议" }));
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    await screen.findByText("3 · 账单类型");
    fireEvent.click(screen.getByRole("button", { name: "确认这些类型并继续" }));

    expect(await screen.findByText("4 · 确认导入")).toBeTruthy();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("退回改映射时保留用户已选的类型，不重置为系统建议", async () => {
    renderPage();
    await upload();
    await screen.findByText("2 · 列对应");
    fireEvent.click(screen.getByRole("button", { name: "全部接受系统建议" }));
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    await screen.findByText("3 · 账单类型");

    fireEvent.click(screen.getByRole("switch", { name: "废弃 证券买入" }));
    fireEvent.click(screen.getByRole("button", { name: "修改对应关系" }));
    await screen.findByText("2 · 列对应");
    fireEvent.click(screen.getByRole("button", { name: "继续" }));
    await waitFor(() => expect(saveMapping).toHaveBeenCalledTimes(2));
    await screen.findByText("3 · 账单类型");

    const discard = screen.getByRole("switch", { name: "废弃 证券买入" });
    expect(discard.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "确认这些类型并继续" }));
    await waitFor(() => expect(saveTypes).toHaveBeenCalledTimes(1));
    expect(saveTypes.mock.calls[0][1].resolutions).toEqual([
      { type_key: "证券买入", category: null, discard: true },
    ]);
  });
});
