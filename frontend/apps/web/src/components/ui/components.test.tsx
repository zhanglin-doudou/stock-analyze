import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  Badge,
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  Input,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from ".";
import { cn } from "@/lib/utils";

afterEach(() => {
  cleanup();
});

describe("UI 包装组件", () => {
  it("Button / Input / Badge 可渲染并保留语义", () => {
    render(
      <div>
        <Button>保存</Button>
        <Input aria-label="证券代码" />
        <Badge variant="success">已完成</Badge>
      </div>,
    );
    expect(screen.getByRole("button", { name: "保存" })).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "证券代码" })).toBeTruthy();
    expect(screen.getByText("已完成")).toBeTruthy();
  });

  it("cn 能用项目语义类覆盖默认字号/圆角", () => {
    expect(cn("tw-text-sm", "tw-text-body")).toBe("tw-text-body");
    expect(cn("tw-rounded-md", "tw-rounded-card")).toBe("tw-rounded-card");
  });

  it("Checkbox 点击后进入选中态", async () => {
    const user = userEvent.setup();
    render(<Checkbox aria-label="同意" />);
    const checkbox = screen.getByRole("checkbox", { name: "同意" });
    await user.click(checkbox);
    expect(checkbox.getAttribute("data-state")).toBe("checked");
  });

  it("Dialog 打开后暴露 dialog/title/description", async () => {
    const user = userEvent.setup();
    render(
      <Dialog>
        <DialogTrigger asChild>
          <Button>打开</Button>
        </DialogTrigger>
        <DialogContent>
          <DialogTitle>确认导入</DialogTitle>
          <DialogDescription>导入后不可撤销。</DialogDescription>
        </DialogContent>
      </Dialog>,
    );
    await user.click(screen.getByRole("button", { name: "打开" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByText("确认导入")).toBeTruthy();
    expect(screen.getByText("导入后不可撤销。")).toBeTruthy();
  });

  it("Tabs 切换后显示对应内容", async () => {
    const user = userEvent.setup();
    render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
          <TabsTrigger value="b">B</TabsTrigger>
        </TabsList>
        <TabsContent value="a">内容 A</TabsContent>
        <TabsContent value="b">内容 B</TabsContent>
      </Tabs>,
    );
    await user.click(screen.getByRole("tab", { name: "B" }));
    expect(screen.getByText("内容 B")).toBeTruthy();
  });

  it("DropdownMenu 打开后暴露 menu/menuitem", async () => {
    const user = userEvent.setup();
    render(
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button>操作</Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem>导出</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    );
    await user.click(screen.getByRole("button", { name: "操作" }));
    expect(await screen.findByRole("menu")).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "导出" })).toBeTruthy();
  });

  it("Popover 打开后显示内容", async () => {
    const user = userEvent.setup();
    render(
      <Popover>
        <PopoverTrigger asChild>
          <Button>筛选</Button>
        </PopoverTrigger>
        <PopoverContent>筛选内容</PopoverContent>
      </Popover>,
    );
    await user.click(screen.getByRole("button", { name: "筛选" }));
    expect(await screen.findByText("筛选内容")).toBeTruthy();
  });

  it("Tooltip hover 后暴露 tooltip 文案", async () => {
    const user = userEvent.setup();
    render(
      <Tooltip delayDuration={0}>
        <TooltipTrigger asChild>
          <Button>帮助</Button>
        </TooltipTrigger>
        <TooltipContent>口径说明</TooltipContent>
      </Tooltip>,
    );
    await user.hover(screen.getByRole("button", { name: "帮助" }));
    expect(await screen.findByRole("tooltip")).toBeTruthy();
    expect(screen.getByText("口径说明")).toBeTruthy();
  });
});
