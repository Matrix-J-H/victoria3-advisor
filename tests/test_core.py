"""Quick checks that need no game files. Run from the project folder:

    python -m unittest discover tests
"""
import tempfile
import time
import unittest
from pathlib import Path

from advisor.advisor import split_reply
from advisor.digest import diff_digests
from advisor.log_events import describe, parse_line
from advisor.save_parser import SaveFile, as_list, field, parse
from advisor.watcher import LogTailer, SaveWatcher

SAMPLE = """SAV0100abc
date=1877.1.1
player_manager={
\tdatabase={
\t\t0={
\t\t\tuser="Someone"
\t\t\tcountry=480
\t\t}
\t}
}
country_manager={
\tdatabase={
\t\t1={
\t\t\tdefinition="GBR"
\t\t}
\t\t16=none
\t\t480={
\t\t\tdefinition="SIL"
\t\t\tbudget={
\t\t\t\tweekly_income={
\t\t\t\t\t0 10.5 20
\t\t\t\t}
\t\t\t\tdiplomacy_expenses={
\t\t\t\t\t43 42=4923.9
\t\t\t\t}
\t\t\t}
\t\t\tcolor=rgb {
\t\t\t\t255 80 71
\t\t\t}
\t\t\tstates={
\t\t\t\t235 767
\t\t\t}
\t\t\tempty={ }
\t\t\tmod={
\t\t\t\t{
\t\t\t\t\tid=1
\t\t\t\t}
\t\t\t\t{
\t\t\t\t\tid=2
\t\t\t\t}
\t\t\t}
\t\t}
\t}
}
"""


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "s.v3"
        self.path.write_bytes(SAMPLE.encode())
        self.save = SaveFile(self.path)

    def tearDown(self):
        self.save.close()
        self.tmp.cleanup()

    def test_top_level(self):
        self.assertEqual(self.save.top("date"), "1877.1.1")
        self.assertEqual(self.save.top("player_manager")["database"]["0"]["country"], 480)

    def test_entries_and_lookup(self):
        self.assertEqual([i for i, _ in self.save.entries("country_manager")], [1, 480])
        body = self.save.entry("country_manager", 480)
        self.assertEqual(field(body, "definition"), "SIL")
        c = parse(body)
        self.assertEqual(c["budget"]["weekly_income"], [0, 10.5, 20])
        self.assertEqual(c["budget"]["diplomacy_expenses"], {"42": 4923.9, "_items": [43]})
        self.assertEqual(c["color"], [255, 80, 71])
        self.assertEqual(c["states"], [235, 767])
        self.assertEqual(as_list(c["empty"]), [])
        self.assertEqual([m["id"] for m in c["mod"]], [1, 2])

    def test_repeated_keys_become_a_list(self):
        self.assertEqual(parse("a=1 a=2 a=3 b=yes"), {"a": [1, 2, 3], "b": True})


class LogTests(unittest.TestCase):
    def test_parse_and_describe(self):
        e = parse_line("[12:00:01][jomini_effect.cpp:1]: V3ADV;play_started;role=target;date=1 May 1877;initiator=France;target=Sierra Leone")
        self.assertEqual(e.kind, "play_started")
        self.assertIn("France", describe(e))
        other = parse_line("x V3ADV;play_started;role=none;initiator=France;target=Japan")
        self.assertIsNone(describe(other))
        m = parse_line("x V3ADV;monthly_economy;gdp=4,219,500.5;balance=-205.1;pool=1.2M;at_war=no")
        self.assertEqual(m.fields, {"gdp": 4219500.5, "balance": -205.1, "pool": 1200000.0, "at_war": False})
        self.assertIsNone(parse_line("[12:00:01] ordinary log line"))


class WatcherTests(unittest.TestCase):
    def test_save_reported_once_when_stable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "autosave.v3"
            p.write_bytes(b"old")
            w = SaveWatcher(p)
            self.assertIsNone(w.poll())
            time.sleep(0.02)
            p.write_bytes(b"new content")
            self.assertIsNone(w.poll())       # first sighting: wait for it to settle
            self.assertEqual(w.poll(), p)     # unchanged since: report
            self.assertIsNone(w.poll())       # and only once

    def test_log_tail_handles_partial_lines_and_truncation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "debug.log"
            p.write_text("old line\n")
            t = LogTailer(p)
            with open(p, "a") as fh:
                fh.write("one\ntw")
            self.assertEqual(t.poll(), ["one"])
            with open(p, "a") as fh:
                fh.write("o\n")
            self.assertEqual(t.poll(), ["two"])
            p.write_text("fresh\n")           # game restarted
            self.assertEqual(t.poll(), ["fresh"])


class AdviceTests(unittest.TestCase):
    def test_split_reply(self):
        a = split_reply("SPOKEN:\nBuild a port.\nThen wait.\n\nDETAIL:\n- treasury fine")
        self.assertEqual(a.spoken, "Build a port. Then wait.")
        self.assertEqual(a.detail, "- treasury fine")

    def test_cut_off_reply_ends_on_a_whole_sentence(self):
        from advisor.advisor import _whole_sentences
        self.assertEqual(_whole_sentences("Build a port. Second, move the Naval Fort"), "Build a port.")

    def test_diff(self):
        old = {"game_date": "1876.7.1", "country": {"tag": "SIL"}, "budget": {"treasury": 100}}
        new = {"game_date": "1877.1.1", "country": {"tag": "SIL"}, "budget": {"treasury": 50}}
        self.assertEqual(diff_digests(old, new)["budget.treasury"], {"was": 100, "now": 50})
        self.assertEqual(diff_digests(old, {"country": {"tag": "GBR"}}), {})


if __name__ == "__main__":
    unittest.main()
