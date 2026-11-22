"""阿里云邮件推送（DirectMail）适配器。

SDK 懒加载：本地/replay 模式未安装 `stock-platform[email]` 也能启动；只有真正
发信时才 import。默认凭据链由 `CredentialClient()` 解析（ECS RAM 角色 / OIDC /
环境变量），代码与配置都不保存 AK。
"""

from __future__ import annotations

import asyncio
from typing import Any

from stock_platform.config import Settings
from stock_platform.mail import EmailDeliveryError, EmailMessage, SendReceipt
from stock_platform.obs import get_logger

log = get_logger("stock_platform.mail.aliyun")


class AliyunDirectMailSender:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from alibabacloud_credentials.client import Client as CredentialClient
            from alibabacloud_dm20151123.client import Client as DmClient
            from alibabacloud_tea_openapi import models as open_api_models

            credential = CredentialClient()
            config = open_api_models.Config(credential=credential)
            config.endpoint = self._settings.email_dm_endpoint
            self._client = DmClient(config)
        return self._client

    def _send_sync(self, message: EmailMessage) -> SendReceipt:
        from alibabacloud_dm20151123 import models as dm_models
        from alibabacloud_tea_util import models as util_models

        client = self._get_client()
        kwargs: dict[str, Any] = {
            "account_name": self._settings.email_dm_account_name,
            "address_type": 1,
            "reply_to_address": False,
            "to_address": message.to,
            "subject": message.subject,
            "html_body": message.html_body,
            "text_body": message.text_body,
            "click_trace": "1",
            "from_alias": self._settings.email_dm_from_alias,
        }
        if message.tag_name:
            kwargs["tag_name"] = message.tag_name
        request = dm_models.SingleSendMailRequest(**kwargs)
        runtime = util_models.RuntimeOptions()
        response = client.single_send_mail_with_options(request, runtime)
        body = getattr(response, "body", None)
        return SendReceipt(
            provider="aliyun",
            request_id=getattr(body, "request_id", None),
            env_id=getattr(body, "env_id", None),
        )

    async def send(self, message: EmailMessage) -> SendReceipt:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._send_sync, message),
                timeout=self._settings.email_dm_timeout_seconds,
            )
        except TimeoutError as exc:
            raise EmailDeliveryError("aliyun dm timeout", provider_code="Timeout") from exc
        except EmailDeliveryError:
            raise
        except Exception as exc:  # SDK 异常类型不稳定，统一折叠
            provider_code = str(getattr(exc, "code", None) or type(exc).__name__)
            recommend = ""
            data = getattr(exc, "data", None)
            if isinstance(data, dict):
                recommend = str(data.get("Recommend") or "")
            log.error(
                "email.aliyun_failed",
                provider_code=provider_code,
                recommend=recommend,
                error=type(exc).__name__,
            )
            raise EmailDeliveryError("aliyun dm send failed", provider_code=provider_code) from exc


__all__ = ["AliyunDirectMailSender"]
