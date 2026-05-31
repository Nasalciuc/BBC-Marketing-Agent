"""Teste Sheets client — mock, fără conexiune reală."""
from services.sheets_client import COLUMNS


def test_columns_match_sheet_headers():
    assert len(COLUMNS) == 13
    assert COLUMNS[0] == "campaign_id"
    assert COLUMNS[-1] == "created_at"
    assert "status" in COLUMNS
    assert "price" in COLUMNS


def test_columns_order():
    assert COLUMNS.index("campaign_id") < COLUMNS.index("status")
    assert COLUMNS.index("event_name") < COLUMNS.index("price")
