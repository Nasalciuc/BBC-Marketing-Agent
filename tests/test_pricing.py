"""Teste pentru pricing engine — verifică formula BBC."""
from services.pricing_engine import calculate_price, get_all_cabin_prices, format_price


class TestPricingEngine:
    """Teste de bază pricing."""

    def test_jfk_to_lhr_business_roundtrip(self):
        """JFK→LHR: NA→EU, round_trip, business."""
        price = calculate_price("JFK", "LHR", "round_trip", "business")
        assert price is not None
        assert price == 1750 + 166 + 117

    def test_jfk_to_nce_business_roundtrip(self):
        """JFK→NCE: NA→EU, round_trip, business."""
        price = calculate_price("JFK", "NCE", "round_trip", "business")
        assert price is not None
        assert price == 1750 + 166 + 153

    def test_jfk_to_nrt_business_roundtrip(self):
        """JFK→NRT: NA→AS, round_trip, business."""
        price = calculate_price("JFK", "NRT", "round_trip", "business")
        assert price is not None
        assert price == 2060 + 166 + 173

    def test_jfk_to_syd_business_roundtrip(self):
        """JFK→SYD: NA→OC, round_trip, business."""
        price = calculate_price("JFK", "SYD", "round_trip", "business")
        assert price is not None
        assert price == 2580 + 166 + 105

    def test_jfk_to_gru_business_roundtrip(self):
        """JFK→GRU: NA→SA, round_trip, business."""
        price = calculate_price("JFK", "GRU", "round_trip", "business")
        assert price is not None

    def test_jfk_to_jnb_business_roundtrip(self):
        """JFK→JNB: NA→AF, round_trip, business."""
        price = calculate_price("JFK", "JNB", "round_trip", "business")
        assert price is not None

    def test_local_route(self):
        """JFK→LAX: NA→NA (LOCAL)."""
        price = calculate_price("JFK", "LAX", "round_trip", "business")
        assert price is not None

    def test_one_way(self):
        """JFK→LHR one-way e mai ieftin decât round-trip."""
        rt = calculate_price("JFK", "LHR", "round_trip", "business")
        ow = calculate_price("JFK", "LHR", "one_way", "business")
        assert rt is not None and ow is not None
        assert ow < rt

    def test_non_na_route_returns_none(self):
        """LHR→NCE (EU→EU) — fără NA, returnează None."""
        price = calculate_price("LHR", "NCE", "round_trip", "business")
        assert price is None

    def test_invalid_airport_returns_none(self):
        """Airport code inexistent."""
        price = calculate_price("JFK", "ZZZ", "round_trip", "business")
        assert price is None

    def test_reverse_route_same_price(self):
        """NCE→JFK ar trebui să fie același preț ca JFK→NCE."""
        price_1 = calculate_price("JFK", "NCE", "round_trip", "business")
        price_2 = calculate_price("NCE", "JFK", "round_trip", "business")
        assert price_1 == price_2

    def test_all_cabin_prices(self):
        """get_all_cabin_prices returnează dict cu 3 clase."""
        prices = get_all_cabin_prices("JFK", "LHR", "round_trip")
        assert prices is not None
        assert "premium_economy" in prices
        assert "business" in prices
        assert "first" in prices
        assert prices["premium_economy"] < prices["business"] < prices["first"]

    def test_hawaii_special_pricing(self):
        """HNL→JFK are pricing special (adaos Hawaii)."""
        price_hnl = calculate_price("HNL", "JFK", "round_trip", "business")
        price_lax = calculate_price("LAX", "JFK", "round_trip", "business")
        assert price_hnl is not None and price_lax is not None
        assert price_hnl > price_lax

    def test_format_price(self):
        assert format_price(2069) == "$2,069"
        assert format_price(2069, "£") == "£2,069"
        assert format_price(116, "€") == "€116"

    def test_case_insensitive(self):
        """Codurile IATA funcționează case-insensitive."""
        price_upper = calculate_price("JFK", "LHR", "round_trip", "business")
        price_lower = calculate_price("jfk", "lhr", "round_trip", "business")
        assert price_upper == price_lower
