import pytest

from app.models import Document
from app.rag.ingest import main


def test_a_folder_that_does_not_exist_is_an_error_not_a_silent_success(tmp_path):
    with pytest.raises(SystemExit) as e:
        main(str(tmp_path / "nope"))
    assert "not a folder" in str(e.value)


def test_a_folder_with_nothing_to_ingest_is_an_error(tmp_path):
    (tmp_path / "notes.exe").write_bytes(b"MZ")
    with pytest.raises(SystemExit) as e:
        main(str(tmp_path))
    assert "no documents found" in str(e.value)


def test_a_folder_of_policies_is_ingested(tmp_path, db, capsys):
    (tmp_path / "refund_policy.md").write_text("# Refund Policy\n\nCustomers may request a refund within 30 days.\n", encoding="utf-8")
    (tmp_path / "ignore.exe").write_bytes(b"MZ")
    main(str(tmp_path))
    assert "ingested refund_policy.md" in capsys.readouterr().out
    assert [d.source for d in db.query(Document)] == ["refund_policy.md"]
