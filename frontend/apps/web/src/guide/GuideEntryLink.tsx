/**
 * 公开页（登录/注册/重置密码）到引导页的反向入口。
 * /guide 是独立静态页、不在 SPA 路由表内，故用 <a> 整页导航而非 <Link>，
 * 避免被 `*` 通配吞掉。样式复用登录页现有的 muted 辅助链接风格。
 */
export function GuideEntryLink() {
  return (
    <p className="muted">
      <a href="/guide">← 使用引导</a>
    </p>
  );
}
