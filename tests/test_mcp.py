import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_real_stdio_all_tools(tmp_path):
    async def scenario():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "personal_memory", "--db", str(tmp_path / "共享.sqlite3"), "serve"],
            env={"PYTHONUTF8": "1"},
        )
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert {tool.name for tool in tools} == {
                "memory_search",
                "memory_context",
                "memory_store",
                "memory_update",
                "memory_forget",
                "memory_history",
                "memory_status",
            }

            async def call(name, arguments):
                result = await session.call_tool(name, arguments)
                assert not result.isError, result
                return json.loads(result.content[0].text)

            record = await call(
                "memory_store",
                {
                    "memory": {
                        "title": "语言偏好",
                        "content": "用户喜欢中文交流",
                        "type": "preference",
                        "source": {"client": "integration-test"},
                    }
                },
            )
            found = await call("memory_search", {"selection": {"query": "中文"}})
            assert found["memories"][0]["id"] == record["id"]
            context = await call("memory_context", {"selection": {}})
            assert len(context["memories"]) == 1
            updated = await call(
                "memory_update",
                {"memory_id": record["id"], "changes": {"importance": 0.9}, "expected_revision": 1},
            )
            assert updated["revision"] == 2
            stale = await session.call_tool(
                "memory_update",
                {"memory_id": record["id"], "changes": {"content": "stale"}, "expected_revision": 1},
            )
            assert stale.isError
            invalid = await session.call_tool(
                "memory_store", {"memory": {"title": "bad", "content": "bad", "scope": "project"}}
            )
            assert invalid.isError
            await call("memory_forget", {"memory_id": record["id"], "expected_revision": 2})
            history = await call("memory_history", {"memory_id": record["id"]})
            assert len(history["history"]) == 3
            status = await call("memory_status", {})
            assert status["forgotten"] == 1
            assert (await call("memory_search", {"selection": {}}))["memories"] == []

    asyncio.run(asyncio.wait_for(scenario(), timeout=40))


def test_real_stdio_new_parameters(tmp_path):
    """query_variants and view must work through the real stdio transport."""

    async def scenario():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "personal_memory", "--db", str(tmp_path / "params.sqlite3"), "serve"],
            env={"PYTHONUTF8": "1"},
        )
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            search_schema = json.dumps(tools["memory_search"].inputSchema, ensure_ascii=False)
            context_schema = json.dumps(tools["memory_context"].inputSchema, ensure_ascii=False)
            assert "query_variants" in search_schema
            assert "view" in context_schema
            assert "compact" in context_schema
            assert "query_variants" in context_schema

            async def call(name, arguments):
                result = await session.call_tool(name, arguments)
                assert not result.isError, result
                return json.loads(result.content[0].text)

            career = await call(
                "memory_store",
                {
                    "memory": {
                        "title": "职业背景：虚构的数字 IC 设计工程师",
                        "content": "用户是数字 IC 设计工程师，目前从事交换芯片相关工作。",
                        "type": "profile",
                        "source": {"kind": "conversation", "client": "integration-test", "date": "2026-09-10"},
                    }
                },
            )
            investment = await call(
                "memory_store",
                {
                    "memory": {
                        "title": "投资背景与分析偏好",
                        "content": "用户偏好长期稳健的配置，关注红利与指数基金。",
                        "type": "preference",
                        "source": {"client": "integration-test"},
                    }
                },
            )

            # The question form still fails; variants recover the record.
            assert (await call("memory_search", {"selection": {"query": "我是做什么工作的"}}))["memories"] == []
            found = await call(
                "memory_search",
                {"selection": {"query": "职业", "query_variants": ["数字 IC", "工作"]}},
            )
            assert found["memories"][0]["id"] == career["id"]
            assert found["memories"][0]["matched_queries"] == [0, 1, 2]

            investment_hits = await call(
                "memory_context",
                {
                    "selection": {"query": "投资", "query_variants": ["稳健", "红利"]},
                    "max_chars": 6000,
                    "view": "compact",
                },
            )
            assert investment_hits["view"] == "compact"
            compact = investment_hits["memories"][0]
            assert compact["id"] == investment["id"]
            assert compact["content"] == "用户偏好长期稳健的配置，关注红利与指数基金。"
            assert compact["source_summary"]["client"] == "integration-test"
            assert "source" not in compact
            assert "notice" in investment_hits

            full = await call("memory_context", {"selection": {"query": "投资"}})
            assert full["view"] == "full"
            assert "source" in full["memories"][0]

            bad = await session.call_tool(
                "memory_search", {"selection": {"query": "投资", "query_variants": ["  "]}}
            )
            assert bad.isError
            bad_view = await session.call_tool(
                "memory_context", {"selection": {"query": "投资"}, "view": "tiny"}
            )
            assert bad_view.isError

    asyncio.run(asyncio.wait_for(scenario(), timeout=40))


def test_real_stdio_returns_diagnostics_and_scope_inventory(tmp_path):
    """The new fields must survive the real stdio transport, not only the store API."""

    async def scenario():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "personal_memory", "--db", str(tmp_path / "diagnostics.sqlite3"), "serve"],
            env={"PYTHONUTF8": "1"},
        )
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()

            async def call(name, arguments):
                result = await session.call_tool(name, arguments)
                assert not result.isError, result
                return json.loads(result.content[0].text)

            # An empty scope is reported as such, and is not a claim about the user.
            empty = await call("memory_search", {"selection": {"query": "稳健"}})
            assert empty["memories"] == []
            assert empty["retrieval"]["reason"] == "empty_scope"
            assert empty["retrieval"]["scoped_active"] == 0

            await call(
                "memory_store",
                {
                    "memory": {
                        "title": "投资背景",
                        "content": "长期稳健的配置，关注红利与指数基金。",
                        "scope": "project",
                        "scope_id": "project:personal-memory",
                        "source": {"client": "integration-test"},
                    }
                },
            )

            # Global scope is still empty, while the project scope holds a record that
            # simply does not match the keywords: two different, actionable reasons.
            assert (
                await call("memory_search", {"selection": {"query": "量子计算"}})
            )["retrieval"]["reason"] == "empty_scope"
            miss = await call(
                "memory_search",
                {
                    "selection": {
                        "query": "量子计算",
                        "scope": "project",
                        "scope_id": "project:personal-memory",
                    }
                },
            )
            assert miss["memories"] == []
            assert miss["retrieval"]["reason"] == "no_lexical_match"
            assert miss["retrieval"]["scoped_active"] == 1
            assert miss["retrieval"]["candidate_pool"] == 0

            hit = await call("memory_search", {"selection": {"query": "稳健"}})
            assert hit["retrieval"]["reason"] == "empty_scope"  # default scope is global

            context = await call(
                "memory_context",
                {
                    "selection": {
                        "query": "投资",
                        "query_variants": ["稳健", "红利"],
                        "scope": "project",
                        "scope_id": "project:personal-memory",
                    },
                    "view": "compact",
                },
            )
            assert context["retrieval"]["strategy"] == "rrf_variants"
            assert context["retrieval"]["variants_used"] == 3
            assert context["returned_after_budget"] == len(context["memories"]) == 1
            assert context["omitted_from_page"] == 0

            status = await call("memory_status", {})
            assert status["scopes"] == [
                {
                    "scope": "project",
                    "scope_id": "project:personal-memory",
                    "total_count": 1,
                    "active_count": 1,
                    "newest_updated_at": status["scopes"][0]["newest_updated_at"],
                }
            ]
            assert status["as_of"]

    asyncio.run(asyncio.wait_for(scenario(), timeout=40))


def test_two_independent_clients_share_memory(tmp_path):
    async def scenario():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "personal_memory", "--db", str(tmp_path / "two-clients.sqlite3"), "serve"],
            env={"PYTHONUTF8": "1"},
        )
        async with stdio_client(params) as (r1, w1), ClientSession(r1, w1) as first:
            await first.initialize()
            async with stdio_client(params) as (r2, w2), ClientSession(r2, w2) as second:
                await second.initialize()
                saved = await first.call_tool(
                    "memory_store",
                    {
                        "memory": {
                            "title": "共享验收",
                            "content": "两个独立客户端共享记忆",
                            "scope": "project",
                            "scope_id": "shared-demo",
                        }
                    },
                )
                assert not saved.isError
                memory_id = json.loads(saved.content[0].text)["id"]
                found = await second.call_tool(
                    "memory_search",
                    {"selection": {"query": "共享记忆", "scope": "project", "scope_id": "shared-demo"}},
                )
                assert not found.isError
                assert json.loads(found.content[0].text)["memories"][0]["id"] == memory_id
                hidden = await second.call_tool(
                    "memory_search", {"selection": {"scope": "project", "scope_id": "other"}}
                )
                assert json.loads(hidden.content[0].text)["memories"] == []

    asyncio.run(asyncio.wait_for(scenario(), timeout=40))
