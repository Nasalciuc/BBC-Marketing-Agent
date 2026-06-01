"""
BBC Pricing Engine — calculează prețuri identic cu site-ul BBC.
Formula: base_continent_pair[trip_type][cabin] + sum_letters(FROM)[cabin] + sum_letters(TO)[cabin]
Sursa datelor: data/price_generator_rules.json + data/airports.json
"""
import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


@lru_cache(maxsize=1)
def _load_rules() -> dict:
    return json.loads((DATA_DIR / "price_generator_rules.json").read_text())


@lru_cache(maxsize=1)
def _load_airports() -> dict:
    airports_list = json.loads((DATA_DIR / "airports.json").read_text())
    return {a["iata_code"]: a for a in airports_list}


def _calc_airport_price(iata_code: str, cabin_class: str) -> int:
    """Sumă valorilor literelor din codul IATA pentru o clasă de cabină."""
    rules = _load_rules()
    total = 0
    for letter in iata_code.upper():
        if letter in rules["letters"]:
            total += rules["letters"][letter][cabin_class]
    return total


def calculate_price(
    from_iata: str,
    to_iata: str,
    trip_type: str = "round_trip",
    cabin: str = "business",
) -> int | None:
    """
    Calculează prețul unui zbor BBC.

    Args:
        from_iata: Cod IATA aeroport plecare (ex: "JFK")
        to_iata: Cod IATA aeroport sosire (ex: "NCE")
        trip_type: "round_trip" sau "one_way"
        cabin: "premium_economy", "business", sau "first"

    Returns:
        Preț în USD sau None dacă ruta nu e suportată
    """
    rules = _load_rules()
    airports = _load_airports()

    from_airport = airports.get(from_iata.upper())
    to_airport = airports.get(to_iata.upper())

    if not from_airport or not to_airport:
        return None

    from_cont = from_airport.get("continent_code", "")
    to_cont = to_airport.get("continent_code", "")

    if from_cont != "NA" and to_cont != "NA":
        return None
    if from_cont == "NA" and to_cont == "NA":
        pair_key = "LOCAL"
    elif from_cont == "NA":
        pair_key = f"NA-{to_cont}"
    else:
        pair_key = f"NA-{from_cont}"

    if pair_key not in rules["continents_pair"]:
        return None

    base_prices = rules["continents_pair"][pair_key]
    if trip_type not in base_prices:
        return None

    base = base_prices[trip_type].get(cabin, 0)

    additional = 0
    if (from_iata.upper() == "HNL" and to_airport.get("country") == "United States") or (
        from_airport.get("country") == "United States" and to_iata.upper() == "HNL"
    ):
        hawaii_extra = {"premium_economy": 600, "business": 1100, "first": 1400}
        additional = hawaii_extra.get(cabin, 0)

    return (
        base
        + additional
        + _calc_airport_price(from_iata, cabin)
        + _calc_airport_price(to_iata, cabin)
    )


def get_all_cabin_prices(
    from_iata: str,
    to_iata: str,
    trip_type: str = "round_trip",
) -> dict[str, int] | None:
    """Returnează prețurile pentru toate clasele de cabină."""
    result = {}
    for cabin in ["premium_economy", "business", "first"]:
        price = calculate_price(from_iata, to_iata, trip_type, cabin)
        if price is None:
            return None
        result[cabin] = price
    return result


def format_price(price: int, currency: str = "$") -> str:
    """Formatează prețul cu separator de mii."""
    return f"{currency}{price:,}"


def format_route_display(from_iata: str, to_iata: str) -> str:
    """Afișează ruta cu oraș destinație: JFK → Nice."""
    airports = _load_airports()
    to_airport = airports.get(to_iata.upper())
    to_name = to_airport.get("city", to_iata.upper()) if to_airport else to_iata.upper()
    return f"{from_iata.upper()} → {to_name}"
