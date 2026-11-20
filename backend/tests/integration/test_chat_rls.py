"""G7 扩展：助手 5 张表的 RLS 直连验证（迁移 0020）。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration]


async def _user(engine, email: str) -> uuid.UUID:
    uid = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "insert into platform.user (id, email, password_hash, role, status) "
                "values (:id, :email, 'hash', 'user', 'active')"
            ),
            {"id": uid, "email": email},
        )
    return uid


async def _seed(engine, uid: uuid.UUID) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "insert into platform.chat_conversation (id, user_id, title) "
                "values ('cnv_rls', :uid, 't')"
            ),
            {"uid": uid},
        )
        await conn.execute(
            text(
                "insert into platform.chat_turn (id, conversation_id, user_id, question) "
                "values ('trn_rls', 'cnv_rls', :uid, 'q')"
            ),
            {"uid": uid},
        )
        await conn.execute(
            text(
                "insert into platform.chat_message (id, conversation_id, user_id, turn_id, role, content) "
                "values ('msg_rls', 'cnv_rls', :uid, 'trn_rls', 'user', 'q')"
            ),
            {"uid": uid},
        )
        await conn.execute(
            text(
                "insert into platform.chat_tool_call "
                "(id, turn_id, user_id, seq, tool_name, status) "
                "values ('tcl_rls', 'trn_rls', :uid, 1, 'data_quality', 'ok')"
            ),
            {"uid": uid},
        )
        await conn.execute(
            text(
                "insert into platform.chat_llm_call "
                "(id, turn_id, user_id, seq, purpose, provider, model, status) "
                "values ('llm_rls', 'trn_rls', :uid, 1, 'answer', 'fake', 'fake', 'ok')"
            ),
            {"uid": uid},
        )


async def _count(app_engine, table: str, tenant: uuid.UUID | None) -> int:
    async with app_engine.begin() as conn:
        if tenant is not None:
            await conn.execute(
                text("select set_config('app.user_id', :uid, true)"), {"uid": str(tenant)}
            )
        return int(await conn.scalar(text(f"select count(*) from platform.{table}")) or 0)


async def test_chat_tables_are_tenant_isolated(owner_engine, app_engine) -> None:
    a = await _user(owner_engine, "chat-rls-a@example.test")
    b = await _user(owner_engine, "chat-rls-b@example.test")
    await _seed(owner_engine, a)
    for table in (
        "chat_conversation",
        "chat_turn",
        "chat_message",
        "chat_tool_call",
        "chat_llm_call",
    ):
        assert await _count(app_engine, table, a) == 1
        assert await _count(app_engine, table, b) == 0
        assert await _count(app_engine, table, None) == 0
