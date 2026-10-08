import unittest
from transfer_sim import Scenario, transfer, benchmark


class TransferTests(unittest.TestCase):
    def test_verified_transfer(self):
        result = transfer(Scenario())
        self.assertEqual(result["verified_owner"], "buyer")
        self.assertTrue(result["settlement_eligible"])

    def test_competitor_capture_cannot_settle(self):
        result = transfer(Scenario(competitors=100, buyer_delay_ms=30))
        self.assertEqual(result["outcome"], "competitor_capture")
        self.assertFalse(result["settlement_eligible"])

    def test_release_rejection_preserves_seller(self):
        self.assertEqual(transfer(Scenario(release_allowed=False))["outcome"], "seller_retained")

    def test_missing_verification_never_settles(self):
        result = transfer(Scenario(verification_available=False))
        self.assertEqual(result["outcome"], "unresolved")
        self.assertFalse(result["settlement_eligible"])

    def test_lost_response_reconciles_by_ownership(self):
        result = transfer(Scenario(lost_claim_response=True))
        self.assertEqual(result["outcome"], "verified")
        self.assertEqual(result["log"][2]["response"], "unknown")

    def test_rejected_buyer_leaves_unresolved_release(self):
        self.assertEqual(transfer(Scenario(buyer_allowed=False))["outcome"], "unresolved")

    def test_reproducibility(self):
        self.assertEqual(benchmark(3, 42), benchmark(3, 42))

    def test_invalid_configuration(self):
        with self.assertRaises(ValueError):
            Scenario(competitors=-1)
        with self.assertRaises(ValueError):
            benchmark(0, 1)


if __name__ == "__main__":
    unittest.main()
