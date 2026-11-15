import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  newIdempotencyKey,
  type ImportBatch,
  type ImportMappingIn,
  type ImportTypesIn,
  type JobAccepted,
  type JobStatus,
  type Preview,
  type RevokeResult,
} from "@sa/api-client";
import { api } from "../client";
import { JobProgress } from "../ui/JobProgress";
import { ProblemView } from "../ui/Problem";
import { RevokeBatchButton } from "../ui/RevokeBatch";
import { Uploader } from "../ui/Uploader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { PortalContainerContext } from "@/components/ui/portal";
import "@/features/import/import.css";
import "./detail.css";

type Step = "upload" | "mapping" | "types" | "confirm" | "result";

interface MappingRow {
  key: string;
  name: string;
  samples: string[];
  level: string | null;
  suggested: string | null;
  suggestedOptions: Record<string, unknown>;
  target: string;
  accepted: boolean;
  ignored: boolean;
}

interface TypeRow {
  typeKey: string;
  label: string;
  rowCount: number;
  amountSum: string;
  samples: string[];
  suggestedCategory: string | null;
  needsConfirmation: boolean;
  category: string;
  discard: boolean;
}

interface TypeChoice {
  category: string;
  discard: boolean;
}

interface QuarantineRow {
  line_no: number | null;
  reason_code: string;
  reason: string;
  date: string | null;
  security_code: string | null;
  summary: string | null;
  amount: string | null;
}

interface CatalogField {
  field: string;
  label: string;
  level: string;
  mergeable: boolean;
}

interface CategoryItem {
  code: string;
  label: string;
}

function readCatalog(preview: Preview): CatalogField[] {
  return (preview.catalog ?? []) as unknown as CatalogField[];
}

function readCategories(preview: Preview): CategoryItem[] {
  return (preview.categories ?? []) as unknown as CategoryItem[];
}

function readTypes(preview: Preview): Record<string, unknown>[] {
  return (preview.types ?? []) as unknown as Record<string, unknown>[];
}

function rowsFromPreview(preview: Preview): MappingRow[] {
  const existing = new Map<string, string>();
  for (const item of (preview.mapping_items ?? []) as unknown as {
    column_key: string;
    field: string;
  }[]) {
    existing.set(item.column_key, item.field);
  }
  const ignored = new Set(preview.ignored_columns ?? []);
  return preview.columns.map((column, index) => {
    const key = column.key ?? `__col_${index}`;
    const isIgnored = ignored.has(key);
    return {
      key,
      name: column.name,
      samples: column.samples ?? column.sample ?? [],
      level: column.level ?? null,
      suggested: column.suggested_field ?? null,
      suggestedOptions: (column.suggested_options ?? {}) as Record<string, unknown>,
      target: existing.get(key) ?? column.suggested_field ?? "",
      // 已保存过映射（含退回改映射）时保持「采用」勾选，不把用户填好的选择清空。
      accepted: existing.has(key) && !isIgnored,
      ignored: isIgnored,
    };
  });
}

function typesFromPreview(preview: Preview, saved?: Map<string, TypeChoice>): TypeRow[] {
  return readTypes(preview).map((item) => {
    const typeKey = String(item.type_key);
    const suggested = (item.suggested_category as string | null) ?? null;
    const remembered = saved?.get(typeKey);
    const category = remembered ? remembered.category : ((item.category as string | null) ?? suggested ?? "");
    const discard = remembered ? remembered.discard : Boolean(item.discard);
    return {
      typeKey,
      label: String(item.type_label ?? item.type_key),
      rowCount: typeof item.row_count === "number" ? item.row_count : 0,
      amountSum: String(item.amount_sum ?? "0.00"),
      samples: (item.samples as string[] | undefined) ?? [],
      suggestedCategory: suggested,
      needsConfirmation: Boolean(item.needs_confirmation),
      category,
      discard,
    };
  });
}

function quarantineRows(preview: Preview): QuarantineRow[] {
  return (preview.quarantine_rows ?? []) as unknown as QuarantineRow[];
}

/** 客户端镜像后端的完整性闸门（只决定按钮可用性，最终以后端 422 为准）。 */
function completeness(rows: MappingRow[]): string[] {
  const fields = new Set(
    rows.filter((row) => row.accepted && !row.ignored && row.target).map((row) => row.target),
  );
  const issues: string[] = [];
  if (!fields.has("trade_date")) issues.push("必须映射成交日期");
  if (!fields.has("amount")) {
    const missing = ["security_code", "quantity", "price"].filter((name) => !fields.has(name));
    if (missing.length > 0) issues.push("需要映射发生金额，或同时映射证券代码/数量/价格");
  }
  if (!fields.has("summary") && !fields.has("side")) {
    issues.push("摘要与买卖标志至少映射一个");
  }
  return issues;
}

export function Import() {
  const [searchParams] = useSearchParams();
  const firstRun = searchParams.get("welcome") === "1";
  const [step, setStep] = useState<Step>("upload");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [rows, setRows] = useState<MappingRow[]>([]);
  const [types, setTypes] = useState<TypeRow[]>([]);
  const [dateFormat, setDateFormat] = useState("");
  const [broker, setBroker] = useState("auto");
  const [reuseTemplate, setReuseTemplate] = useState(true);
  const [confirmUnmapped, setConfirmUnmapped] = useState(false);
  const [ackReimport, setAckReimport] = useState(false);
  const [job, setJob] = useState<JobAccepted | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [batch, setBatch] = useState<ImportBatch | null>(null);
  const [batchError, setBatchError] = useState<unknown>(null);
  const [revokeNotice, setRevokeNotice] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState("");
  const [portal, setPortal] = useState<HTMLElement | null>(null);
  const [quarantineNotice, setQuarantineNotice] = useState<QuarantineRow[] | null>(null);
  const idempotency = useRef<string>("");
  // 退回改映射时用来回填用户此前选好的类型分类（C）；按预览 id 隔离，换文件不串。
  const typeChoices = useRef<{ previewId: string; choices: Map<string, TypeChoice> }>({
    previewId: "",
    choices: new Map(),
  });

  // Portal 容器必须带 data-import-ui，弹层才吃到 scoped reset。
  useEffect(() => {
    const node = document.createElement("div");
    node.id = "import-portal";
    node.setAttribute("data-import-ui", "");
    document.body.appendChild(node);
    setPortal(node);
    return () => {
      node.remove();
      setPortal(null);
    };
  }, []);

  useEffect(() => {
    if (!preview) return;
    typeChoices.current = {
      previewId: preview.preview_id,
      choices: new Map(
        types.map((row) => [row.typeKey, { category: row.category, discard: row.discard }]),
      ),
    };
  }, [types, preview]);

  const applyPreview = useCallback(
    (dto: Preview, options?: { restore?: boolean }) => {
      const remembered =
        typeChoices.current.previewId === dto.preview_id
          ? typeChoices.current.choices
          : undefined;
      setPreview(dto);
      setDateFormat(dto.date_format ?? "");
      setRows(rowsFromPreview(dto));
      setTypes(typesFromPreview(dto, remembered));
      idempotency.current = newIdempotencyKey();
      // A/B：进入可提交阶段时，隔离行非 0 才弹「不会导入的行」提醒；无隔离行不出现这一步。
      const quarantined = quarantineRows(dto);
      if (dto.stage === "ready") {
        setStep("confirm");
        setQuarantineNotice(
          !options?.restore && quarantined.length > 0 ? quarantined : null,
        );
      } else if (dto.stage === "types") {
        setStep("types");
        setQuarantineNotice(null);
      } else if (dto.stage === "mapping") {
        setStep("mapping");
        setQuarantineNotice(null);
      } else {
        setStep("upload");
        setQuarantineNotice(null);
      }
    },
    [],
  );

  async function onFile(file: File): Promise<void> {
    setBusy(true);
    setError(null);
    setBatch(null);
    setBatchError(null);
    setRevokeNotice("");
    setDone("");
    setJob(null);
    setBatchId(null);
    try {
      const dto = await api.imports.preview(file, {
        flow: "v2",
        broker,
        reuse_template: reuseTemplate,
      });
      applyPreview(dto);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const issues = useMemo(() => completeness(rows), [rows]);
  const pendingColumns = useMemo(
    () => rows.filter((row) => !row.ignored && (!row.accepted || !row.target)),
    [rows],
  );
  const unresolvedTypes = useMemo(
    () => types.filter((row) => !row.discard && !row.category),
    [types],
  );

  async function saveMapping(ack: boolean): Promise<void> {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      const items: ImportMappingIn["items"] = rows
        .filter((row) => row.accepted && !row.ignored && row.target)
        .map((row) => ({
          column_key: row.key,
          target_field: row.target,
          options: row.target === "amount" ? row.suggestedOptions : {},
        }));
      const body: ImportMappingIn = {
        mapping_version: preview.mapping_version,
        items: items ?? [],
        ignored: rows.filter((row) => row.ignored).map((row) => row.key),
        acknowledge_unmapped: ack,
        date_format: dateFormat || null,
        broker: preview.broker,
      };
      const dto = await api.imports.saveMapping(
        preview.preview_id,
        body,
        String(preview.mapping_version),
      );
      applyPreview(dto);
    } catch (err) {
      setError(err);
      if (preview) void refreshPreview(preview.preview_id);
    } finally {
      setBusy(false);
    }
  }

  async function refreshPreview(previewId: string): Promise<void> {
    try {
      applyPreview(await api.imports.getPreview(previewId), { restore: true });
    } catch {
      // 刷新失败保留当前草稿，错误由主流程展示。
    }
  }

  async function saveTypes(): Promise<void> {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      const body: ImportTypesIn = {
        mapping_version: preview.mapping_version,
        resolutions: types.map((row) => ({
          type_key: row.typeKey,
          category: row.discard ? null : row.category || null,
          discard: row.discard,
        })),
      };
      const dto = await api.imports.saveTypes(
        preview.preview_id,
        body,
        String(preview.mapping_version),
      );
      applyPreview(dto);
    } catch (err) {
      setError(err);
      if (preview) void refreshPreview(preview.preview_id);
    } finally {
      setBusy(false);
    }
  }

  async function commit(): Promise<void> {
    if (!preview) return;
    setBusy(true);
    setError(null);
    setBatch(null);
    setBatchError(null);
    setRevokeNotice("");
    try {
      const accepted = await api.imports.commit(
        {
          preview_id: preview.preview_id,
          mode: "full",
          acknowledge_reimport: ackReimport,
        },
        idempotency.current,
      );
      setJob(accepted);
      setBatchId(accepted.batch_id ?? null);
      setStep("result");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const loadBatch = useCallback(async (id: string) => {
    setBatchError(null);
    try {
      setBatch(await api.imports.getBatch(id));
    } catch (err) {
      setBatchError(err);
    }
  }, []);

  const onJobDone = useCallback(
    (status: JobStatus) => {
      setDone(`任务结束：${status}`);
      if (status !== "succeeded" || !batchId) return;
      void loadBatch(batchId);
    },
    [batchId, loadBatch],
  );

  const onRevoked = useCallback(
    (result: RevokeResult) => {
      const mapping = result.mapping_revoked;
      const detail = mapping?.mapping_deleted ? "（已同时删除本次导入建立的列对应关系）" : "";
      setRevokeNotice(
        `已撤销本次导入（批次 ${result.batch_id}）：回退流水 ${result.reverted.inserted ?? 0} 行${detail}。`,
      );
      void loadBatch(result.batch_id);
    },
    [loadBatch],
  );

  const catalog = useMemo(() => (preview ? readCatalog(preview) : []), [preview]);
  const categoryOptions = useMemo(() => (preview ? readCategories(preview) : []), [preview]);
  const catalogGroups = useMemo(() => {
    const required = catalog.filter((item) => item.level === "L1" || item.level === "L1-trade");
    const common = catalog.filter((item) => item.level !== "L1" && item.level !== "L1-trade");
    return [
      { label: "必填", items: required },
      { label: "常用 / 其他", items: common },
    ];
  }, [catalog]);

  function patchRow(key: string, patch: Partial<MappingRow>): void {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  }

  function patchType(key: string, patch: Partial<TypeRow>): void {
    setTypes((current) => current.map((row) => (row.typeKey === key ? { ...row, ...patch } : row)));
  }

  return (
    <PortalContainerContext.Provider value={portal}>
      <div className="detail-page" data-import-ui="">
        <header className="detail-head">
          <div className="detail-eyebrow">数据 · 导入账单</div>
          <h1 className="detail-title">导入账单</h1>
          <p className="detail-sub">
            上传 → 列对应 → 账单类型 → 确认导入；列对应会记住并复用，未确认的列不会写入。
          </p>
        </header>

        {firstRun && !batch ? (
          <section className="import-welcome" role="status" aria-live="polite">
            <span className="import-welcome-kicker">开始使用</span>
            <h2>先上传一份账单，再开始复盘</h2>
            <p>
              复盘需要真实的交易流水：上传券商导出的账单后，系统会自动识别列、生成回合、净值和指标。
              没有账单时，后面的看板都会是空的。
            </p>
            <ol className="import-welcome-steps">
              <li>选择账单文件（.xls / .xlsx / .csv，≤20MB）</li>
              <li>逐项确认列对应与账单类型</li>
              <li>确认导入，自动建立回合与看板</li>
            </ol>
          </section>
        ) : null}

        <Card className="tw-mb-4">
          <CardHeader>
            <CardTitle>1 · 上传</CardTitle>
            <CardDescription>支持 .xls / .xlsx / .csv，≤20MB；券商默认自动识别。</CardDescription>
          </CardHeader>
          <CardContent className="tw-flex tw-flex-col tw-gap-3">
            <div className="tw-flex tw-items-center tw-gap-3">
              <Label htmlFor="import-broker">券商</Label>
              <Select value={broker} onValueChange={setBroker}>
                <SelectTrigger id="import-broker" aria-label="券商" className="tw-w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">自动识别</SelectItem>
                  <SelectItem value="huatai">华泰证券</SelectItem>
                  <SelectItem value="generic">通用</SelectItem>
                </SelectContent>
              </Select>
              <Label htmlFor="import-reuse">复用已确认的列对应</Label>
              <Switch
                id="import-reuse"
                aria-label="复用已确认的列对应"
                checked={reuseTemplate}
                onCheckedChange={setReuseTemplate}
              />
            </div>
            <Uploader disabled={busy} onFile={(file) => void onFile(file)} />
          </CardContent>
        </Card>

        <ProblemView error={error} onRetry={preview ? () => void commit() : undefined} />

        {preview && preview.stage === "legacy" ? (
          <Card>
            <CardContent className="tw-pt-4">
              <p role="alert">这次预览由旧版本创建，请重新上传以使用新的分步确认流程。</p>
            </CardContent>
          </Card>
        ) : null}

        {preview && step === "mapping" ? (
          <Card className="tw-mb-4">
            <CardHeader>
              <CardTitle>2 · 列对应</CardTitle>
              <CardDescription>
                文件：{preview.file.name}（{preview.file.bytes} 字节，哈希 {preview.file.sha256.slice(0, 8)}）
                {preview.template ? " · 已复用你确认过的列对应，可修改" : ""}
              </CardDescription>
            </CardHeader>
            <CardContent className="tw-flex tw-flex-col tw-gap-3">
              <div className="tw-flex tw-items-center tw-gap-3">
                <Label htmlFor="import-date-format">日期格式</Label>
                <Select value={dateFormat || "auto"} onValueChange={(value) => setDateFormat(value === "auto" ? "" : value)}>
                  <SelectTrigger id="import-date-format" aria-label="日期格式" className="tw-w-44">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">自动（{preview.date_format ?? "未探测"}）</SelectItem>
                    <SelectItem value="YYYYMMDD">YYYYMMDD</SelectItem>
                    <SelectItem value="YYYY-MM-DD">YYYY-MM-DD</SelectItem>
                    <SelectItem value="YYYY/M/D">YYYY/M/D</SelectItem>
                    <SelectItem value="YYYY-DD-MM">YYYY-DD-MM</SelectItem>
                  </SelectContent>
                </Select>
                <Button
                  variant="secondary"
                  type="button"
                  onClick={() =>
                    setRows((current) =>
                      current.map((row) =>
                        row.suggested
                          ? { ...row, target: row.suggested, accepted: true, ignored: false }
                          : row,
                      ),
                    )
                  }
                >
                  全部接受系统建议
                </Button>
              </div>

              <Table aria-label="列对应">
                <TableHeader>
                  <TableRow>
                    <TableHead>表格列（样例）</TableHead>
                    <TableHead>数据库列</TableHead>
                    <TableHead>系统建议</TableHead>
                    <TableHead>采用</TableHead>
                    <TableHead>忽略本列</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.key}>
                      <TableCell>
                        <div>{row.name}</div>
                        <div className="tw-text-xs tw-text-muted-foreground">{row.samples.join(" / ")}</div>
                      </TableCell>
                      <TableCell>
                        <Select
                          value={row.target || "unset"}
                          onValueChange={(value) =>
                            patchRow(row.key, {
                              target: value === "unset" ? "" : value,
                              accepted: value !== "unset",
                              ignored: false,
                            })
                          }
                        >
                          <SelectTrigger aria-label={`字段 ${row.name}`} className="tw-w-44">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="unset">（不映射）</SelectItem>
                            {catalogGroups.map((group) =>
                              group.items.length === 0 ? null : (
                                <SelectGroup key={group.label}>
                                  <SelectLabel>{group.label}</SelectLabel>
                                  {group.items.map((item) => (
                                    <SelectItem key={item.field} value={item.field}>
                                      {item.label}（{item.level}）
                                    </SelectItem>
                                  ))}
                                </SelectGroup>
                              ),
                            )}
                          </SelectContent>
                        </Select>
                      </TableCell>
                      <TableCell>
                        {row.suggested ? (
                          <Badge variant="secondary">
                            {catalog.find((item) => item.field === row.suggested)?.label ?? row.suggested}
                          </Badge>
                        ) : (
                          <span className="tw-text-muted-foreground">（未识别）</span>
                        )}
                        {row.target === "other_fee" && rows.filter((r) => r.target === "other_fee" && r.accepted && !r.ignored).length > 1 ? (
                          <Badge variant="outline" className="tw-ml-2">
                            合并 {rows.filter((r) => r.target === "other_fee" && r.accepted && !r.ignored).length} 列，求和
                          </Badge>
                        ) : null}
                      </TableCell>
                      <TableCell>
                        <Switch
                          aria-label={`采用 ${row.name} → ${row.target || "未选择"}`}
                          checked={row.accepted && !row.ignored}
                          onCheckedChange={(checked) => patchRow(row.key, { accepted: checked, ignored: false })}
                        />
                      </TableCell>
                      <TableCell>
                        <Switch
                          aria-label={`忽略 ${row.name}`}
                          checked={row.ignored}
                          onCheckedChange={(checked) => patchRow(row.key, { ignored: checked, accepted: false })}
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              {issues.length > 0 ? (
                <ul role="alert" className="tw-text-sm tw-text-destructive">
                  {issues.map((issue) => (
                    <li key={issue}>{issue}</li>
                  ))}
                </ul>
              ) : null}
              <p className="tw-text-xs tw-text-muted-foreground">
                未确认/未映射列 {pendingColumns.length} 个；继续前需确认它们不会导入。
              </p>
              <div className="tw-flex tw-justify-end tw-gap-2">
                <Button
                  type="button"
                  disabled={busy || issues.length > 0}
                  onClick={() => {
                    if (pendingColumns.length > 0) setConfirmUnmapped(true);
                    else void saveMapping(false);
                  }}
                >
                  继续
                </Button>
              </div>
            </CardContent>
          </Card>
        ) : null}

        {preview && step === "types" ? (
          <Card className="tw-mb-4">
            <CardHeader>
              <CardTitle>3 · 账单类型</CardTitle>
              <CardDescription>
                系统建议只做预填，不会自动通过；核对后点击下方按钮整步确认（只涉及本文件的类型）。
              </CardDescription>
            </CardHeader>
            <CardContent className="tw-flex tw-flex-col tw-gap-3">
              <Table aria-label="账单类型">
                <TableHeader>
                  <TableRow>
                    <TableHead>类型</TableHead>
                    <TableHead className="tw-text-right">行数</TableHead>
                    <TableHead className="tw-text-right">金额合计</TableHead>
                    <TableHead>样例</TableHead>
                    <TableHead>Category</TableHead>
                    <TableHead>废弃</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {types.map((row) => (
                    <TableRow key={row.typeKey}>
                      <TableCell>
                        {row.label}
                        {row.suggestedCategory && row.category === row.suggestedCategory ? (
                          <Badge variant="outline" className="tw-ml-2">
                            系统建议
                          </Badge>
                        ) : null}
                      </TableCell>
                      <TableCell className="tw-text-right">{row.rowCount}</TableCell>
                      <TableCell className="tw-text-right tw-font-mono">{row.amountSum}</TableCell>
                      <TableCell className="tw-text-xs tw-text-muted-foreground">
                        {row.samples.join(" / ")}
                      </TableCell>
                      <TableCell>
                        <Select
                          value={row.category || "none"}
                          onValueChange={(value) =>
                            patchType(row.typeKey, { category: value === "none" ? "" : value, discard: false })
                          }
                          disabled={row.discard}
                        >
                          <SelectTrigger aria-label={`分类 ${row.label}`} className="tw-w-36">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="none">（未选择）</SelectItem>
                            {categoryOptions.map((item) => (
                              <SelectItem key={item.code} value={item.code}>
                                {item.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </TableCell>
                      <TableCell>
                        <Switch
                          aria-label={`废弃 ${row.label}`}
                          checked={row.discard}
                          onCheckedChange={(checked) =>
                            patchType(row.typeKey, { discard: checked, category: checked ? "" : row.category })
                          }
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {unresolvedTypes.length > 0 ? (
                <p role="alert" className="tw-text-sm tw-text-destructive">
                  还有 {unresolvedTypes.length} 个类型未处理。
                </p>
              ) : null}
              <div className="tw-flex tw-justify-end tw-gap-2">
                <Button type="button" variant="secondary" onClick={() => setStep("mapping")}>
                  修改对应关系
                </Button>
                <Button type="button" disabled={busy || unresolvedTypes.length > 0} onClick={() => void saveTypes()}>
                  确认这些类型并继续
                </Button>
              </div>
            </CardContent>
          </Card>
        ) : null}

        {preview && step === "confirm" ? (
          <Card className="tw-mb-4">
            <CardHeader>
              <CardTitle>4 · 确认导入</CardTitle>
              <CardDescription>所有计数与预估均来自后端，前端只展示。</CardDescription>
            </CardHeader>
            <CardContent className="tw-flex tw-flex-col tw-gap-3">
              <dl className="tw-grid tw-grid-cols-2 tw-gap-2 tw-text-sm">
                <dt>文件</dt>
                <dd>{preview.file.name}</dd>
                <dt>券商</dt>
                <dd>{preview.broker}</dd>
                <dt>映射来源</dt>
                <dd>{preview.template ? "已复用模板" : "手动确认"}</dd>
                <dt>忽略列</dt>
                <dd>{preview.ignored_columns?.length ?? 0}</dd>
                <dt>预估</dt>
                <dd>
                  解析 {preview.estimated.parsed} · 写入 {preview.estimated.inserted} · 跳过{" "}
                  {preview.estimated.skipped} · 隔离 {preview.estimated.quarantined} · 废弃{" "}
                  {preview.estimated.discarded}
                </dd>
              </dl>
              {(preview.blocking ?? []).length > 0 ? (
                <ul role="alert" className="tw-text-sm tw-text-destructive">
                  {(preview.blocking ?? []).map((item) => (
                    <li key={`${item.code}-${item.field ?? ""}`}>{item.message}</li>
                  ))}
                </ul>
              ) : null}
              {preview.requires_reimport_ack ? (
                <div role="alert" className="tw-rounded-md tw-border tw-border-border tw-p-3">
                  <p className="tw-text-sm">同一文件曾用不同的列对应导入过，直接重导可能产生重复行。</p>
                  <div className="tw-mt-2 tw-flex tw-items-center tw-gap-2">
                    <Switch
                      id="import-ack-reimport"
                      aria-label="确认重新导入"
                      checked={ackReimport}
                      onCheckedChange={setAckReimport}
                    />
                    <Label htmlFor="import-ack-reimport">确认重新导入</Label>
                  </div>
                </div>
              ) : null}
              <div className="tw-flex tw-justify-between tw-gap-2">
                <div className="tw-flex tw-gap-2">
                  <Button type="button" variant="secondary" onClick={() => setStep("mapping")}>
                    修改对应关系
                  </Button>
                  {types.length > 0 ? (
                    <Button type="button" variant="secondary" onClick={() => setStep("types")}>
                      修改账单类型
                    </Button>
                  ) : null}
                </div>
                <Button
                  type="button"
                  disabled={busy || !preview.can_commit || (preview.requires_reimport_ack && !ackReimport)}
                  onClick={() => void commit()}
                >
                  {busy ? "提交中…" : "确认导入"}
                </Button>
              </div>
            </CardContent>
          </Card>
        ) : null}

        {step === "result" && job ? (
          <Card className="tw-mb-4">
            <CardHeader>
              <CardTitle>5 · 导入结果</CardTitle>
            </CardHeader>
            <CardContent className="tw-flex tw-flex-col tw-gap-3">
              <JobProgress jobId={job.job_id} onDone={onJobDone} />
              {done ? <p className="notice">{done}</p> : null}
              {batchError ? (
                <ProblemView error={batchError} onRetry={batchId ? () => void loadBatch(batchId) : undefined} />
              ) : null}
              {batch ? (
                <div className="batch-result">
                  <p>
                    批次号 <code>{batch.batch_id}</code> · 插入 {batch.counts.inserted} 行 · 隔离{" "}
                    {batch.counts.quarantined} 行 · 跳过 {batch.counts.skipped} 行
                    {batch.counts.discarded ? ` · 废弃 ${batch.counts.discarded} 行` : ""}
                  </p>
                  {batch.mapping?.remap_required ? (
                    <p role="alert" className="notice-warn">
                      费用合并列的余额链未完全对上，建议核对「其他费/过户费」的对应关系后重新导入。
                    </p>
                  ) : null}
                  {(batch.filled_count ?? 0) > 0 ? (
                    <div className="notice-warn" role="alert">
                      <strong>账单存在缺失：系统已自动补差 {batch.filled_count} 笔</strong>
                      <ul>
                        {(batch.fills ?? []).map((fill) => (
                          <li key={fill.trade_id}>
                            第 {fill.line_no ?? "?"} 行 · {fill.trade_date} · 金额 {fill.amount} · 补差后余额{" "}
                            {fill.cash_balance ?? "—"} · {fill.summary ?? "对账自动补差"}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  <p className="muted">
                    回合已由后台自动按 FIFO 建立（导入完成后可在「回合」查看）。
                  </p>
                  <div className="import-actions">
                    <Link className="btn btn-ghost" to={`/imports?batch=${batch.batch_id}`}>
                      查看批次
                    </Link>
                    {batch.can_revoke ? (
                      <RevokeBatchButton batchId={batch.batch_id} label="撤销本次导入" onRevoked={onRevoked} />
                    ) : (
                      <span className="muted">该批次已不可撤销（{batch.status}）。</span>
                    )}
                    <Link className="btn btn-ghost" to="/trades">
                      去交易流水核对
                    </Link>
                    <Link className="btn btn-ghost" to="/rounds">
                      查看回合
                    </Link>
                    <Link className="btn btn-ghost" to="/dashboard">
                      去看板
                    </Link>
                  </div>
                  {batch.mapping_source !== "legacy" ? (
                    <p className="muted">
                      本次列对应已保存{batch.mapping?.is_origin ? "（首次导入，撤销会一并删除）" : "（增量导入，撤销保留列对应）"}，
                      下次同格式账单会自动复用。
                    </p>
                  ) : null}
                  {revokeNotice ? <p className="notice">{revokeNotice}</p> : null}
                </div>
              ) : null}
            </CardContent>
          </Card>
        ) : null}

        <AlertDialog open={confirmUnmapped} onOpenChange={setConfirmUnmapped}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>这些列不会导入</AlertDialogTitle>
              <AlertDialogDescription>
                未确认/未映射列：{pendingColumns.map((row) => row.name).join("、") || "无"}。确认继续吗？
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>取消</AlertDialogCancel>
              <AlertDialogAction
                onClick={() => {
                  setConfirmUnmapped(false);
                  void saveMapping(true);
                }}
              >
                确认继续
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
        <AlertDialog
          open={quarantineNotice !== null}
          onOpenChange={(open) => {
            if (!open) setQuarantineNotice(null);
          }}
        >
          <AlertDialogContent className="tw-max-w-3xl">
            <AlertDialogHeader>
              <AlertDialogTitle>有 {quarantineNotice?.length ?? 0} 行不会导入</AlertDialogTitle>
              <AlertDialogDescription>
                下列行无法解析或属于重复行，会进入隔离区；不影响其余行提交，仅供提交前核对。
              </AlertDialogDescription>
            </AlertDialogHeader>
            <div className="tw-max-h-72 tw-overflow-auto">
              <Table aria-label="隔离行明细">
                <TableHeader>
                  <TableRow>
                    <TableHead>行号</TableHead>
                    <TableHead>日期</TableHead>
                    <TableHead>代码</TableHead>
                    <TableHead>摘要</TableHead>
                    <TableHead className="tw-text-right">金额</TableHead>
                    <TableHead>隔离原因</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(quarantineNotice ?? []).map((row, index) => (
                    <TableRow key={`${row.line_no ?? "?"}-${index}`}>
                      <TableCell className="tw-whitespace-nowrap">{row.line_no ?? "—"}</TableCell>
                      <TableCell className="tw-whitespace-nowrap">{row.date ?? "—"}</TableCell>
                      <TableCell className="tw-whitespace-nowrap">{row.security_code ?? "—"}</TableCell>
                      <TableCell>{row.summary ?? "—"}</TableCell>
                      <TableCell className="tw-text-right tw-font-mono">{row.amount ?? "—"}</TableCell>
                      <TableCell>{row.reason}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <AlertDialogFooter>
              <AlertDialogCancel
                onClick={() => {
                  setQuarantineNotice(null);
                  setStep(types.length > 0 ? "types" : "mapping");
                }}
              >
                返回修改
              </AlertDialogCancel>
              <AlertDialogAction onClick={() => setQuarantineNotice(null)}>
                知悉，继续导入
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </PortalContainerContext.Provider>
  );
}
