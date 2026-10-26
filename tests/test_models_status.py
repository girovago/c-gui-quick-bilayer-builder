"""Test di JobStatus: parsing tollerante di check_status."""

from __future__ import annotations

from cgui_studio.models import JobStatus

PENDING = {
    "jobid": "9285768142",
    "rank": "Your job is pending, 19 job(s) ahead",
    "status": "pending",
    "rqinfo": "q1",
    "pidfile": "/tmp/x.pid",
    "hasTarFile": False,
    "lastOutFile": "out.txt",
    "lastOutLine": 12,
    "lastOutTime": "2026-10-24 12:00",
    "lastInpStep": 3,
}

DONE = {"jobid": "42", "status": "done", "hasTarFile": True, "lastInpStep": 7}


def test_pending_full_parse():
    st = JobStatus.from_json(PENDING)
    assert st.is_terminal is False
    assert st.has_tar is False
    assert st.last_inp_step == 3
    assert st.rank_position == 19


def test_done_is_terminal():
    st = JobStatus.from_json(DONE)
    assert st.is_terminal
    assert st.rank_position is None


def test_error_terminal_with_detail():
    st = JobStatus.from_json({"jobid": "1", "status": "error", "error": "boom"})
    assert st.is_terminal
    assert st.error == "boom"


def test_unknown_status_does_not_crash():
    st = JobStatus.from_json({"jobid": "1", "status": "warping"})
    assert st.status == "warping"
    assert st.is_terminal is False


def test_missing_and_malformed_fields_tolerated():
    st = JobStatus.from_json({"hasTarFile": "1", "lastInpStep": "5", "lastOutLine": "x"})
    assert st.has_tar is True
    assert st.last_inp_step == 5
    assert st.last_out_line is None  # non numerico → None, non crash
    assert st.jobid is None

    st2 = JobStatus.from_json("not-a-dict")
    assert st2.status is None and st2.error  # messaggio d'errore conservato

    st3 = JobStatus.from_json(None, jobid="99")
    assert st3.jobid == "99"


def test_fallback_jobid():
    st = JobStatus.from_json({"status": "done"}, jobid="77")
    assert st.jobid == "77"
