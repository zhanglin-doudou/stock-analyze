"""邮件发送端口与本地实现（生产适配器见 mail_aliyun.py）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from stock_platform.config import Settings
from stock_platform.obs import get_logger

log = get_logger("stock_platform.mail")


class EmailDeliveryError(RuntimeError):
    """邮件发送失败；provider_code 仅用于日志/指标，不返回给用户。"""

    def __init__(self, message: str, *, provider_code: str = "unknown", retryable: bool = True):
        super().__init__(message)
        self.provider_code = provider_code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class SendReceipt:
    provider: str
    request_id: str | None = None
    env_id: str | None = None


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    html_body: str
    text_body: str
    tag_name: str | None = None
    # 仅本地 LogEmailSender / 测试 Fake 使用；Aliyun 适配器忽略。
    code: str | None = None


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> SendReceipt: ...


def mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    if not domain:
        return "***"
    visible = local[:1] if local else ""
    return f"{visible}***@{domain}"


class LogEmailSender:
    """本地开发用：把验证码打到日志，禁止在生产启用（启动时校验）。"""

    async def send(self, message: EmailMessage) -> SendReceipt:
        log.info(
            "email.dev_send",
            to=mask_email(message.to),
            subject=message.subject,
            code=message.code,
            tag=message.tag_name,
        )
        return SendReceipt(provider="log", request_id=None)


class NullEmailSender:
    """功能关闭/测试占位；被调用即抛错，避免静默丢信。"""

    async def send(self, message: EmailMessage) -> SendReceipt:
        raise EmailDeliveryError("email sender is not configured", provider_code="not_configured")


def create_email_sender(settings: Settings) -> EmailSender:
    if settings.email_provider == "aliyun":
        from stock_platform.mail_aliyun import AliyunDirectMailSender

        return AliyunDirectMailSender(settings)
    if settings.email_provider == "log":
        if settings.env == "prod":
            raise RuntimeError("生产环境禁止 EMAIL_PROVIDER=log（会把验证码打进日志）")
        return LogEmailSender()
    return NullEmailSender()


__all__ = [
    "EmailDeliveryError",
    "EmailMessage",
    "EmailSender",
    "LogEmailSender",
    "NullEmailSender",
    "SendReceipt",
    "create_email_sender",
    "mask_email",
]
