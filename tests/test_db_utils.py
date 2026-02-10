"""Tests for db/utils.py — response normalisation helpers."""

from agent.db.utils import first_or_none, normalise_response


# ---------------------------------------------------------------------------
# normalise_response
# ---------------------------------------------------------------------------


def test_normalise_none_returns_empty_list():
    """None input gives an empty list."""
    assert normalise_response(None) == []


def test_normalise_empty_list_returns_empty_list():
    """An empty list stays an empty list."""
    assert normalise_response([]) == []


def test_normalise_single_dict_wraps_in_list():
    """A plain dict (from create/upsert) becomes a one-element list."""
    record = {"id": "instrument:1", "symbol": "AAPL"}
    assert normalise_response(record) == [record]


def test_normalise_list_of_dicts_passes_through():
    """A list[dict] (from select/insert) passes through unchanged."""
    records = [{"id": "a"}, {"id": "b"}]
    assert normalise_response(records) == records


def test_normalise_query_wrapped_result():
    """A query-style [{"result": [...]}] is unwrapped to the inner list."""
    inner = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
    wrapped = [{"result": inner, "status": "OK"}]
    assert normalise_response(wrapped) == inner


def test_normalise_query_wrapped_single_dict():
    """A query-style [{"result": {dict}}] is unwrapped to [dict]."""
    inner = {"symbol": "AAPL"}
    wrapped = [{"result": inner, "status": "OK"}]
    assert normalise_response(wrapped) == [inner]


def test_normalise_query_wrapped_none_result():
    """A query-style [{"result": None}] returns []."""
    wrapped = [{"result": None, "status": "OK"}]
    assert normalise_response(wrapped) == []


def test_normalise_unexpected_type_returns_empty():
    """A completely unexpected type returns []."""
    assert normalise_response("unexpected") == []


def test_normalise_list_of_non_dicts_returns_empty():
    """A list of non-dict items (e.g. ints) returns []."""
    assert normalise_response([1, 2, 3]) == []


# ---------------------------------------------------------------------------
# first_or_none
# ---------------------------------------------------------------------------


def test_first_or_none_returns_first_record():
    """Returns the first dict from a list."""
    records = [{"id": "1"}, {"id": "2"}]
    assert first_or_none(records) == {"id": "1"}


def test_first_or_none_single_dict():
    """Returns the dict itself when the input is a single dict."""
    record = {"id": "1", "value": 42}
    assert first_or_none(record) == record


def test_first_or_none_empty_list():
    """Returns None for an empty list."""
    assert first_or_none([]) is None


def test_first_or_none_none_input():
    """Returns None for None input."""
    assert first_or_none(None) is None


def test_first_or_none_query_wrapped():
    """Unwraps a query-style response and returns the first record."""
    inner = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
    wrapped = [{"result": inner}]
    assert first_or_none(wrapped) == {"symbol": "AAPL"}
