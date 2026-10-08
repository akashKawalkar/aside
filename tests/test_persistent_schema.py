from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from context.persistent_schema import ALWAYS_ON, SECTIONS, Entry, PersistentFile, empty_file, load_persistent_file

DOCS = Path(__file__).resolve().parent.parent / "docs"
T0 = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)


def entry(text="Lives in Pune.", **kw):
    return {"text": text, "created": T0, **kw}


def test_starter_file_loads_and_has_every_section_empty():
    f = load_persistent_file(DOCS / "persistent_file.starter.json")
    assert list(f.sections) == list(SECTIONS) and all(not s.entries for s in f.sections.values())


def test_the_example_in_the_doc_is_valid():
    block = re.search(r"```json\n(.*?)```", (DOCS / "persistent_file.md").read_text(encoding="utf-8"), re.S).group(1)
    f = PersistentFile.model_validate(json.loads(block))
    assert f.sections["identity"].entries[0].text == "Lives in Pune and works in IST."


def test_entry_defaults_and_tidying():
    e = Entry(**entry("  Lives   in\nPune.  "))
    assert (e.text, e.source, e.status, e.evidence_count, e.last_confirmed) == ("Lives in Pune.", "user", "active", 1, None)


@pytest.mark.parametrize("bad", [
    {"text": ""}, {"text": "   "}, {"text": "x" * 501},
    {"source": "robot"}, {"status": "deleted"}, {"evidence_count": 0},
    {"created": datetime(2026, 10, 6, 10, 0)},                              # naive time
    {"last_confirmed": T0 - timedelta(days=1)},                              # before it was created
    {"extra_field": 1},                                                      # exactly the six fields
])
def test_invalid_entries_are_rejected(bad):
    with pytest.raises(ValidationError):
        Entry(**{**entry(), **bad})


def test_only_the_five_known_sections_exist_and_all_are_always_present():
    f = PersistentFile.model_validate({"sections": {"goals": {"entries": [entry("Ship M3.")]}}})
    assert list(f.sections) == ["identity", "preferences", "routine", "goals", "people"]
    assert len(f.sections["goals"].entries) == 1 and not f.sections["identity"].entries
    with pytest.raises(ValidationError, match="unknown sections"):
        PersistentFile.model_validate({"sections": {"hobbies": {"entries": []}}})


def test_core_sections_are_the_always_on_ones():
    assert ALWAYS_ON == ("identity", "preferences", "routine")


def test_duplicate_live_entries_are_rejected_but_a_retired_copy_is_fine():
    with pytest.raises(ValidationError, match="duplicate"):
        PersistentFile.model_validate({"sections": {"identity": {"entries": [entry("Lives in Pune."), entry("lives in pune.")]}}})
    f = PersistentFile.model_validate({"sections": {"identity": {"entries": [entry("Lives in Pune.", status="retired"), entry("lives in pune.")]}}})
    assert len(f.sections["identity"].entries) == 2


def test_live_excludes_retired_and_keeps_provisional():
    f = PersistentFile.model_validate({"sections": {"identity": {"entries": [
        entry("Old city.", status="retired"), entry("New city.", status="provisional"), entry("Name is A.")]}}})
    assert [e.text for e in f.live("identity")] == ["New city.", "Name is A."]


def test_round_trip_through_json_is_lossless(tmp_path):
    f = PersistentFile.model_validate({"sections": {"people": {"entries": [entry("Rahul is a colleague.", source="note", evidence_count=3, last_confirmed=T0 + timedelta(days=2))]}}})
    path = tmp_path / "p.json"
    path.write_text(f.model_dump_json(), encoding="utf-8")
    assert load_persistent_file(path) == f
    assert empty_file().sections.keys() == SECTIONS.keys()
