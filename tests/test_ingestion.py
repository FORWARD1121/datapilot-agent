from io import BytesIO

import pandas as pd
import pytest
from openpyxl import Workbook

from app.core import AppError, Settings
from app.db import Store
from app.ingestion import parse_upload


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path)


def excel(rows):
    book = Workbook()
    for row in rows:
        book.active.append(row)
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_csv_types_and_no_silent_dedup(settings):
    parsed = parse_upload("s.csv", b"date,region,sales_amount\n2026-01-01,N,100\n2026-01-01,N,100\n", settings)
    assert len(parsed.frame) == 2
    assert parsed.audit["duplicate_rows_detected"] == 1
    assert pd.api.types.is_datetime64_any_dtype(parsed.frame.date)
    assert parsed.frame.sales_amount.sum() == 200


def test_explicit_dedup(settings):
    parsed = parse_upload("s.csv", b"a,b\n1,2\n1,2\n", settings, True)
    assert len(parsed.frame) == 1
    assert parsed.audit["duplicate_rows_removed"] == 1


def test_excel(settings):
    parsed = parse_upload("s.xlsx", excel([["region", "value"], ["N", 42]]), settings)
    assert parsed.frame.value.iloc[0] == 42


def test_excel_formula_rejected(settings):
    with pytest.raises(AppError, match="formulas"):
        parse_upload("s.xlsx", excel([["a", "b"], ["=1+1", 2]]), settings)


@pytest.mark.parametrize("name,raw", [
    ("../x.csv", b"a,b\n1,2"), ("x.exe", b"a,b\n1,2"), ("x.csv", b""),
    ("x.csv", b"a,a\n1,2"), ("x.csv", b"a,b\n1,2,3"),
    ("x.csv", b"free text only"), ("x.csv", b"a,b\n,\n"),
    ("x.xlsx", b"not an Excel file"), ("x.csv", b"a,b\n\x00,2"),
])
def test_bad_uploads(settings, name, raw):
    with pytest.raises(AppError):
        parse_upload(name, raw, settings)


def test_bounds(settings):
    settings.max_rows = 1
    with pytest.raises(AppError) as exc:
        parse_upload("s.csv", b"a,b\n1,2\n3,4", settings)
    assert exc.value.status == 413


def test_identifiers_and_leading_zero(settings):
    parsed = parse_upload("s.csv", b"order_id,postal,value\n123,00123,5", settings)
    assert parsed.frame.order_id.iloc[0] == "123"
    assert parsed.frame.postal.iloc[0] == "00123"


def test_encoding(settings):
    raw = "区域,金额\n华北,100".encode("gb18030")
    parsed = parse_upload("s.csv", raw, settings)
    assert parsed.frame["区域"].iloc[0] == "华北"


def test_roundtrip_and_task_storage(settings):
    store = Store(settings)
    parsed = parse_upload("s.csv", b"date,value\n2026-01-01,10.25", settings)
    record = store.save_dataset(parsed.frame, "s.csv", parsed.audit)
    pd.testing.assert_frame_equal(parsed.frame, store.frame(record.id), check_dtype=False)
    task_id = store.create_task(record.id, "summary")
    store.finish_task(task_id, {"steps": []}, [], {"summary": "ok"}, [])
    assert store.task(task_id)["status"] == "completed"
    store.engine.dispose()


def test_nonexistent_dataset(settings):
    store = Store(settings)
    with pytest.raises(AppError) as exc:
        store.frame("../../secret")
    assert exc.value.status == 404
    store.engine.dispose()
