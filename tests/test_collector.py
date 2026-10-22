import unittest

from collector import parse_probe_output, classify_gpu, classify_server

SAMPLE_BUSY = """===GPUS===
0, GPU-aaa, NVIDIA RTX A6000, 47421, 49140, 50
1, GPU-bbb, NVIDIA RTX A6000, 94, 49140, 0
===PROCS===
GPU-aaa, 12345, 47000
===UPTIME===
 17:39:37 up 58 days,  2:59, 14 users,  load average: 6.01, 5.98, 6.19
===CORES===
32
===USERS===
12345 alice
"""

SAMPLE_IDLE = """===GPUS===
0, GPU-ccc, NVIDIA RTX A6000, 1, 49140, 0
1, GPU-ddd, NVIDIA RTX A6000, 1, 49140, 0
===PROCS===
===UPTIME===
 17:39:37 up 100 days, 17:31, 13 users,  load average: 0.05, 0.10, 0.12
===CORES===
64
===USERS===
"""


class TestParseProbeOutput(unittest.TestCase):
    def test_busy_server(self):
        info = parse_probe_output(SAMPLE_BUSY)
        self.assertEqual(len(info["gpus"]), 2)
        g0 = info["gpus"][0]
        self.assertEqual(g0["index"], 0)
        self.assertEqual(g0["memory_used"], 47421)
        self.assertEqual(g0["memory_total"], 49140)
        self.assertEqual(g0["utilization"], 50)
        self.assertEqual(g0["users"], ["alice"])
        self.assertEqual(info["gpus"][1]["users"], [])
        self.assertAlmostEqual(info["load"], 6.01)
        self.assertEqual(info["cores"], 32)
        self.assertIn("up 58 days", info["uptime"])

    def test_idle_server(self):
        info = parse_probe_output(SAMPLE_IDLE)
        self.assertEqual(len(info["gpus"]), 2)
        self.assertEqual(info["gpus"][0]["users"], [])
        self.assertAlmostEqual(info["load"], 0.05)
        self.assertEqual(info["cores"], 64)

    def test_garbage_input(self):
        info = parse_probe_output("command not found")
        self.assertEqual(info["gpus"], [])
        self.assertIsNone(info["cores"])

    def test_cores_not_a_number(self):
        text = SAMPLE_BUSY.replace("===CORES===\n32", "===CORES===\nnproc: not found")
        info = parse_probe_output(text)
        self.assertIsNone(info["cores"])

    def test_proc_user_unknown(self):
        # ps may print nothing for a vanished pid
        text = SAMPLE_BUSY.replace("12345 alice", "12345")
        info = parse_probe_output(text)
        self.assertEqual(info["gpus"][0]["users"], [])

    def test_duplicate_users_deduped(self):
        text = SAMPLE_BUSY.replace(
            "===PROCS===\nGPU-aaa, 12345, 47000",
            "===PROCS===\nGPU-aaa, 12345, 23000\nGPU-aaa, 12346, 24000",
        ).replace("12345 alice", "12345 alice\n12346 alice")
        info = parse_probe_output(text)
        self.assertEqual(info["gpus"][0]["users"], ["alice"])


class TestClassify(unittest.TestCase):
    def test_gpu_occupied_by_memory(self):
        self.assertEqual(
            classify_gpu({"memory_used": 47421, "memory_total": 49140, "utilization": 0}),
            "occupied",
        )

    def test_gpu_occupied_by_utilization(self):
        self.assertEqual(
            classify_gpu({"memory_used": 100, "memory_total": 49140, "utilization": 60}),
            "occupied",
        )

    def test_gpu_free(self):
        self.assertEqual(
            classify_gpu({"memory_used": 433, "memory_total": 49140, "utilization": 0}),
            "free",
        )

    def test_server_free(self):
        self.assertEqual(classify_server(["free", "free"]), "free")

    def test_server_partial(self):
        self.assertEqual(classify_server(["occupied", "free"]), "partial")

    def test_server_occupied(self):
        self.assertEqual(classify_server(["occupied", "occupied"]), "occupied")

    def test_server_no_gpus_is_unknown(self):
        self.assertEqual(classify_server([]), "unknown")


if __name__ == "__main__":
    unittest.main()
