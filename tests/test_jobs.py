import unittest

from jobs import parse_duration, parse_gpus, parse_jobs_output

SAMPLE = """\
===QUEUE===
142107_0|C100_IDN_DUALHEAD|rtx8000|RUNNING|2:26|3-00:00:00|aisurrey20|N/A|4|12G|2026-10-27T12:28:33
142107_1|C100_IDN_DUALHEAD|rtx8000|PENDING|0:00|3-00:00:00|(Resources)|N/A|4|12G|2026-10-27T12:28:33
142005_[0-4]|sweep|3090|PENDING|0:00|1-00:00:00|(Priority)|gres/gpu:2|8|24G|2026-10-27T11:02:10
===HISTORY===
139576|train_big|rtx8000|COMPLETED|6:10:03|2026-10-27T12:26:21|billing=229,cpu=16,gres/gpu=1,gres/gpu:quadro_rtx_8000=1,mem=64G,node=1|billing=229,cpu=16,gres/gpu=1,mem=64G,node=1
141986|debug_run|rtx8000|FAILED|0:00:12|2026-10-27T11:50:15|billing=12,cpu=4,mem=16G,node=1|billing=12,cpu=4,mem=16G,node=1
142107_0|C100_IDN_DUALHEAD|rtx8000|RUNNING|0:02:26|Unknown|billing=193,cpu=4,gres/gpu=1,node=1|billing=193,cpu=4,gres/gpu=1,node=1
142107_1|C100_IDN_DUALHEAD|rtx8000|PENDING|0:00:00|Unknown||billing=193,cpu=4,gres/gpu=1,node=1
140001|old_one|3090|CANCELLED by 1234|1:00:00|2026-10-27T09:00:00|cpu=8,gres/gpu=2,node=1|cpu=8,gres/gpu=2,node=1
"""


class TestParseDuration(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(parse_duration("2:26"), 146)
        self.assertEqual(parse_duration("6:10:03"), 22203)
        self.assertEqual(parse_duration("3-00:00:00"), 259200)
        self.assertEqual(parse_duration("1-02:03:04"), 93784)
        self.assertIsNone(parse_duration("UNLIMITED"))
        self.assertIsNone(parse_duration("N/A"))
        self.assertIsNone(parse_duration(""))


class TestParseGpus(unittest.TestCase):
    def test_squeue_tres(self):
        self.assertEqual(parse_gpus("gres/gpu:1"), 1)
        self.assertEqual(parse_gpus("gres/gpu:a100:2"), 2)
        self.assertEqual(parse_gpus("N/A"), 0)

    def test_sacct_alloctres(self):
        self.assertEqual(
            parse_gpus("billing=229,cpu=16,gres/gpu=1,gres/gpu:quadro_rtx_8000=1,mem=64G"), 1
        )
        self.assertEqual(parse_gpus("cpu=4,mem=16G,node=1"), 0)


class TestParseJobsOutput(unittest.TestCase):
    def setUp(self):
        self.data = parse_jobs_output(SAMPLE)

    def test_running(self):
        self.assertEqual(len(self.data["running"]), 1)
        job = self.data["running"][0]
        self.assertEqual(job["id"], "142107_0")
        self.assertEqual(job["where"], "aisurrey20")
        self.assertEqual(job["gpus"], 1)
        self.assertEqual(job["elapsed_s"], 146)
        self.assertEqual(job["limit_s"], 259200)

    def test_pending_reason_unwrapped(self):
        self.assertEqual(len(self.data["pending"]), 2)
        self.assertEqual(self.data["pending"][0]["where"], "Resources")
        self.assertEqual(self.data["pending"][1]["gpus"], 2)

    def test_gpus_fall_back_to_sacct_when_squeue_says_na(self):
        # both queue rows had %b = N/A; counts come from sacct TRES
        self.assertEqual(self.data["running"][0]["gpus"], 1)
        self.assertEqual(self.data["pending"][0]["gpus"], 1)

    def test_history_excludes_running_and_sorts_newest_first(self):
        states = [j["state"] for j in self.data["history"]]
        self.assertNotIn("RUNNING", states)
        self.assertEqual(
            [j["id"] for j in self.data["history"]], ["139576", "141986", "140001"]
        )
        self.assertEqual(self.data["history"][2]["state"], "CANCELLED")

    def test_garbage(self):
        empty = parse_jobs_output("no such command")
        self.assertEqual(empty, {"running": [], "pending": [], "history": []})


if __name__ == "__main__":
    unittest.main()
