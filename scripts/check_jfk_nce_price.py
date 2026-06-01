import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.pricing_engine import format_price, get_all_cabin_prices

prices = get_all_cabin_prices("JFK", "NCE", "round_trip")
print("JFK → NCE round-trip:")
print(f"  Premium Economy: {format_price(prices['premium_economy'])}")
print(f"  Business:        {format_price(prices['business'])}")
print(f"  First:           {format_price(prices['first'])}")
print(f"\nFormula check: 1750 + 166 + 153 = {1750 + 166 + 153}")
print(f"Match: {prices['business'] == 2069}")
