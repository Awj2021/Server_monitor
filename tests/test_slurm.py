import unittest

from slurm import parse_sinfo_output, pretty_gpu, node_available

SAMPLE = """\
aisurrey01               2080ti*                  mix         28/36/0/64      gpu:nvidia_geforce_rtx_2080_ti:6(S:0-1)      gpu:nvidia_geforce_rtx_2080_ti:4(IDX:0-1,4-5)
aisurrey07               2080ti*                  drain*      0/0/64/64       gpu:nvidia_geforce_rtx_2080_ti:7(S:0-1)      gpu:nvidia_geforce_rtx_2080_ti:0(IDX:N/A)
aisurrey11               3090                     alloc       60/4/0/64      gpu:nvidia_geforce_rtx_3090:7(S:0-1)         gpu:nvidia_geforce_rtx_3090:7(IDX:0-6)
aisurrey14               3090                     drain       0/0/64/64      gpu:nvidia_geforce_rtx_3090:8(S:0-1)         gpu:nvidia_geforce_rtx_3090:0(IDX:N/A)
aisurrey21               a100                     mix         39/25/0/64     gpu:nvidia_a100-sxm4-80gb:4(S:0-1)           gpu:nvidia_a100-sxm4-80gb:1(IDX:0)
aisurrey15               deadpart                 drain       0/0/64/64      gpu:nvidia_geforce_rtx_3090:8(S:0-1)         gpu:nvidia_geforce_rtx_3090:0(IDX:N/A)
"""


class TestParseSinfo(unittest.TestCase):
    def setUp(self):
        self.parts = {p["name"]: p for p in parse_sinfo_output(SAMPLE)}

    def test_partition_names_and_default(self):
        self.assertEqual(
            set(self.parts), {"2080ti", "3090", "a100", "deadpart"}
        )
        self.assertTrue(self.parts["2080ti"]["default"])
        self.assertFalse(self.parts["3090"]["default"])

    def test_free_counts_exclude_drained_nodes(self):
        p = self.parts["2080ti"]
        # drained aisurrey07 contributes nothing; aisurrey01 has 6-4=2 free
        self.assertEqual(p["gpus_free"], 2)
        self.assertEqual(p["gpus_avail_total"], 6)
        self.assertEqual(p["gpus_offline"], 7)
        self.assertEqual(p["status"], "free")

    def test_fully_allocated_partition(self):
        p = self.parts["3090"]
        self.assertEqual(p["gpus_free"], 0)
        self.assertEqual(p["status"], "full")

    def test_all_nodes_down(self):
        self.assertEqual(self.parts["deadpart"]["status"], "down")

    def test_gpu_type_prettified(self):
        self.assertEqual(self.parts["a100"]["gpu_type"], "A100 80GB")
        self.assertEqual(self.parts["2080ti"]["gpu_type"], "RTX 2080 Ti")

    def test_node_detail(self):
        node = self.parts["a100"]["nodes"][0]
        self.assertEqual(node["name"], "aisurrey21")
        self.assertEqual(node["gpus_free"], 3)
        self.assertEqual(node["cpus_alloc"], 39)
        self.assertEqual(node["cpus_idle"], 25)
        self.assertEqual(node["cpus_total"], 64)

    def test_garbage_input(self):
        self.assertEqual(parse_sinfo_output("sinfo: command not found"), [])


class TestHelpers(unittest.TestCase):
    def test_node_available(self):
        self.assertTrue(node_available("mix"))
        self.assertTrue(node_available("idle"))
        self.assertTrue(node_available("alloc"))
        self.assertFalse(node_available("drain"))
        self.assertFalse(node_available("drain*"))
        self.assertFalse(node_available("drng"))
        self.assertFalse(node_available("down*"))

    def test_pretty_gpu_fallback(self):
        self.assertEqual(pretty_gpu(None), "GPU")
        self.assertEqual(pretty_gpu("nvidia_h100_sxm"), "H100 SXM")


if __name__ == "__main__":
    unittest.main()
