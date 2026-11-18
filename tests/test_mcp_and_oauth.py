"""Conformance tests for OAuth 2.1 / Agent Identity JWTs and the MCP Toolkit Server."""

from __future__ import annotations

import importlib.util
import unittest

from mcp_toolkit.server import dispatch_mcp_jsonrpc, read_mcp_resource
from trading_agent.protocols.oauth_config import (
    agent_identity_header_provider,
    get_oauth_protected_resource_metadata,
    mint_oauth_jwt,
    verify_oauth_jwt,
)


class TestMcpToolkitAndOAuth(unittest.TestCase):
    def test_oauth_jwt_mint_verify_and_scope_enforcement(self) -> None:
        read_only_token = mint_oauth_jwt(scopes=["trading:read"])
        ok_read, claims = verify_oauth_jwt(read_only_token, required_scope="trading:read")
        self.assertTrue(ok_read)
        assert isinstance(claims, dict)
        self.assertEqual(claims["identity_type"], "AGENT_IDENTITY")

        ok_exec, reason = verify_oauth_jwt(read_only_token, required_scope="trading:execute")
        self.assertFalse(ok_exec)
        self.assertIn("Insufficient scope", str(reason))

        expired_token = mint_oauth_jwt(scopes=["trading:read"], ttl_seconds=-10)
        ok_exp, exp_reason = verify_oauth_jwt(expired_token, required_scope="trading:read")
        self.assertFalse(ok_exp)
        self.assertIn("expired", str(exp_reason))

    def test_mcp_jsonrpc_initialize_tools_and_resources(self) -> None:
        init_res = dispatch_mcp_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(init_res["result"]["serverInfo"]["name"], "alphapulse-quant-mcp-toolkit")

        list_res = dispatch_mcp_jsonrpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tool_names = {t["name"] for t in list_res["result"]["tools"]}
        self.assertIn("stream_realtime_market_ticks", tool_names)
        self.assertIn("issue_ap2_cart_mandate", tool_names)
        self.assertIn("settle_ap2_paper_trade", tool_names)

        blotter = read_mcp_resource("ui://alphapulse/trading-blotter")
        self.assertEqual(blotter["mimeType"], "text/html")
        self.assertIn("AlphaPulse Paper Blotter", blotter["html"])

    def test_mcp_tool_call_enforces_oauth_bearer_header(self) -> None:
        unauth = dispatch_mcp_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "stream_realtime_market_ticks", "arguments": {"symbol": "GOOGL"}},
            },
            authorization_header=None,
            enforce_oauth=True,
        )
        self.assertEqual(unauth["error"]["code"], -32001)

        headers = agent_identity_header_provider(None)
        auth_call = dispatch_mcp_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "stream_realtime_market_ticks", "arguments": {"symbol": "GOOGL"}},
            },
            authorization_header=headers["Authorization"],
            enforce_oauth=True,
        )
        self.assertEqual(auth_call["result"]["structuredContent"]["symbol"], "GOOGL")

        meta = get_oauth_protected_resource_metadata("https://alphapulse.run.app")
        self.assertTrue(meta["agent_identity_supported"])

    @unittest.skipUnless(importlib.util.find_spec("mcp") is not None, "mcp SDK not installed")
    def test_fastmcp_server_instantiation(self) -> None:
        from mcp_toolkit.server import create_fastmcp_server

        server = create_fastmcp_server()
        self.assertEqual(server.name, "alphapulse-quant-mcp-toolkit")


if __name__ == "__main__":
    unittest.main()
