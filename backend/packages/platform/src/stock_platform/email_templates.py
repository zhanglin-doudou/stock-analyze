"""注册/找回密码邮件模板（纯函数，无 I/O）。

模板变量只来自固定配置与 6 位数字验证码；不把用户邮箱放进正文。
HTML 采用表格 + 内联样式，兼顾 Gmail / QQ / Outlook；纯文本用于送达率。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

_CODE_RE = re.compile(r"^\d{6}$")

_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>__TITLE__</title>
</head>
<body style="margin:0;padding:0;background:#f6f5f0;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
    __PREHEADER__
  </div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f6f5f0;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:560px;background:#fbfaf6;border:1px solid #e3e1d6;border-radius:14px;overflow:hidden;">
          <tr><td style="height:6px;background:#3b5f4b;font-size:0;line-height:0;">&nbsp;</td></tr>
          <tr>
            <td style="padding:32px 32px 8px;font-family:-apple-system,'SF Pro Text','PingFang SC','Microsoft YaHei',sans-serif;color:#1b1d19;">
              <p style="margin:0 0 8px;font-size:12px;line-height:1.4;letter-spacing:.08em;color:#5b6057;text-transform:uppercase;">__BRAND__ · 账户安全</p>
              <h1 style="margin:0 0 12px;font-size:24px;line-height:1.3;font-weight:600;color:#1b1d19;">__HEADING__</h1>
              <p style="margin:0;font-size:14px;line-height:1.7;color:#3c4038;">__INTRO__</p>
            </td>
          </tr>
          <tr>
            <td style="padding:24px 32px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="background:#f1efe8;border:1px solid #e3e1d6;border-radius:10px;">
                <tr>
                  <td align="center" style="padding:20px 12px;">
                    <div style="font-family:'SF Mono','JetBrains Mono',Menlo,Consolas,monospace;font-size:36px;line-height:1.1;letter-spacing:8px;font-weight:600;color:#1b1d19;white-space:nowrap;">__CODE__</div>
                    <div style="margin-top:8px;font-size:12px;line-height:1.5;color:#5b6057;">__TTL__ 分钟内有效 · 仅可使用一次</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:0 32px 28px;font-family:-apple-system,'SF Pro Text','PingFang SC','Microsoft YaHei',sans-serif;">
              <p style="margin:0 0 12px;font-size:13px;line-height:1.7;color:#3c4038;">__SAFETY__</p>
              <p style="margin:0;font-size:12px;line-height:1.7;color:#5b6057;">__EXTRA__</p>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 32px;background:#f1efe8;border-top:1px solid #e3e1d6;font-family:-apple-system,'SF Pro Text','PingFang SC','Microsoft YaHei',sans-serif;">
              <p style="margin:0;font-size:11px;line-height:1.6;color:#686d63;">此邮件由系统自动发送，请勿直接回复。<br>__BRAND__ · __SITE__</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

_TEXT = """__BRAND__ · __TITLE__

你的__KIND__验证码是：__CODE__
__TTL__ 分钟内有效，仅可使用一次。

__SAFETY__
__EXTRA__
请勿将验证码转发给他人；__BRAND__不会通过电话、短信或邮件向你索要验证码。

此邮件由系统自动发送，请勿直接回复。
__BRAND__ · __SITE__
"""


@dataclass(frozen=True, slots=True)
class EmailContent:
    subject: str
    html: str
    text: str


def _render(
    *,
    code: str,
    ttl_seconds: int,
    brand: str,
    site_host: str,
    title: str,
    heading: str,
    kind: str,
    intro: str,
    safety: str,
    extra: str,
) -> EmailContent:
    if not _CODE_RE.fullmatch(code):
        raise ValueError("验证码必须是 6 位数字")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds 必须为正")
    ttl_minutes = max(1, ttl_seconds // 60)
    safe_brand = html.escape(brand, quote=True)
    safe_site = html.escape(site_host, quote=True)
    values = {
        "__TITLE__": html.escape(title),
        "__PREHEADER__": html.escape(f"你的{kind}验证码是 {code}，{ttl_minutes} 分钟内有效。"),
        "__BRAND__": safe_brand,
        "__HEADING__": html.escape(heading),
        "__INTRO__": intro,
        "__CODE__": code,
        "__TTL__": str(ttl_minutes),
        "__SAFETY__": safety,
        "__EXTRA__": extra,
        "__SITE__": safe_site,
        "__KIND__": html.escape(kind),
    }
    html_body = _HTML
    text_body = _TEXT
    for key, value in values.items():
        html_body = html_body.replace(key, value)
        text_body = text_body.replace(key, value)
    subject = f"【{brand}】{title}（{ttl_minutes} 分钟内有效）"
    return EmailContent(subject=subject, html=html_body, text=text_body)


def render_registration_email(
    *, code: str, ttl_seconds: int, brand: str, site_host: str
) -> EmailContent:
    return _render(
        code=code,
        ttl_seconds=ttl_seconds,
        brand=brand,
        site_host=site_host,
        title="注册验证码",
        heading="验证你的邮箱",
        kind="注册",
        intro=f"你正在注册{html.escape(brand, quote=False)}。请在注册页面输入下面的验证码。",
        safety="如果这不是你本人的操作，请忽略这封邮件，你的账户不会被创建。",
        extra="为了账户安全，请勿将验证码转发给他人。",
    )


def render_password_reset_email(
    *, code: str, ttl_seconds: int, brand: str, site_host: str
) -> EmailContent:
    return _render(
        code=code,
        ttl_seconds=ttl_seconds,
        brand=brand,
        site_host=site_host,
        title="重置密码验证码",
        heading="重置你的密码",
        kind="重置密码",
        intro=f"你正在重置{html.escape(brand, quote=False)}的登录密码。请在找回密码页面输入下面的验证码。",
        safety="如果这不是你本人的操作，请忽略这封邮件，你的密码不会被修改。",
        extra="重置成功后，所有已登录设备将退出，API Token 将失效。",
    )


__all__ = [
    "EmailContent",
    "render_password_reset_email",
    "render_registration_email",
]
