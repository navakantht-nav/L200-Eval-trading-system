"""Protocol conformance tests for AP2 v0.2 mandates, A2UI v0.9 surfaces, and A2A cards."""

from __future__ import annotations

import unittest

from trading_agent.protocols.a2a_integration import (
    build_agent_card_dict,
    handle_macro_a2a_rpc,
)
from trading_agent.protocols.a2ui_surfaces import (
    A2UI_EXTENSION_URI_V09,
    emit_a2ui_trading_surface,
)
from trading_agent.protocols.ap2_mandates import (
    AP2_EXTENSION_URI,
    create_broker_cart_mandate,
    sign_user_ap2_mandates,
    verify_ap2_double_lock,
)
from trading_agent.tools import reset_process_portfolio


class TestAp2MandatesAndTamperDetection(unittest.TestCase):
    def setUp(self) -> None:
        reset_process_portfolio()

    def test_ap2_double_lock_happy_path_and_price_tamper_rejection(self) -> None:
        from trading_agent.protocols.ap2_mandates import settle_ap2_paper_trade

        cart = create_broker_cart_mandate(
            symbol="GOOGL",
            side="BUY",
            quantity=15,
            locked_limit_price=192.50,
        )
        checkout, payment = sign_user_ap2_mandates(cart, trader_principal="user:nav")
        valid, msg = verify_ap2_double_lock(cart, checkout, payment)
        self.assertTrue(valid, msg)

        # Tamper with locked price after signing -> double-lock MUST reject!
        tampered_cart = cart.model_copy(update={"locked_limit_price": 250.00})
        valid_tampered, tamper_msg = verify_ap2_double_lock(tampered_cart, checkout, payment)
        self.assertFalse(valid_tampered)
        self.assertIn("tampered", tamper_msg)

        # Settle untampered mandate
        settled = settle_ap2_paper_trade(
            cart_mandate=cart.model_dump(),
            checkout_mandate=checkout.model_dump(),
            payment_mandate=payment.model_dump(),
            order_type="LIMIT",
        )
        self.assertEqual(settled["status"], "success")
        self.assertTrue(settled["ap2_verification"]["verified"])


class TestA2uiAndA2aProtocols(unittest.TestCase):
    def test_a2ui_v09_declarative_surface_structure(self) -> None:
        surface = emit_a2ui_trading_surface(symbol="NVDA", surface_kind="MARKET_PULSE")
        self.assertEqual(surface["protocol"], "A2UI/v0.9")
        msgs = surface["a2ui_messages"]
        self.assertEqual(len(msgs), 3)
        self.assertIn("createSurface", msgs[0])
        self.assertIn("updateComponents", msgs[1])
        self.assertIn("updateDataModel", msgs[2])
        self.assertEqual(surface["mcp_ui_widget"]["provider"], "mcp")

    def test_a2a_agent_card_advertises_extensions_and_oauth(self) -> None:
        card = build_agent_card_dict("https://alphapulse.example.com")
        self.assertTrue(card["capabilities"]["streaming"])
        ext_uris = {e["uri"] for e in card["capabilities"]["extensions"]}
        self.assertIn(A2UI_EXTENSION_URI_V09, ext_uris)
        self.assertIn(AP2_EXTENSION_URI, ext_uris)
        self.assertIn("alphapulse_oauth2", card["securitySchemes"])

        rpc_resp = handle_macro_a2a_rpc(
            {
                "jsonrpc": "2.0",
                "id": "req-7",
                "method": "message/send",
                "params": {"message": {"parts": [{"text": "Macro catalyst check for NVDA"}]}},
            }
        )
        self.assertEqual(rpc_resp["result"]["status"]["state"], "completed")


if __name__ == "__main__":
    unittest.main()
