"""Offline regressions for breadth-first host-family alternatives."""
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "src/plugin.video.streamhub/resources/streamhub/source_selection.py"
spec = importlib.util.spec_from_file_location("source_selection_test", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
select_diverse = module.select_diverse


def candidate(rank, family, index):
    return (rank, family, index, family.upper(), "https://example.invalid/" + family + str(index))


class DiverseSelectionTests(unittest.TestCase):
    def test_first_round_includes_sixteen_families_before_any_duplicate(self):
        candidates = [candidate(rank, str(rank), index)
                      for rank in range(20) for index in range(3)]
        selected = select_diverse(candidates)
        self.assertEqual([item[1] for item in selected], [str(rank) for rank in range(16)])
        self.assertEqual(len(selected), 16)

    def test_rounds_preserve_family_rank_and_order_within_family(self):
        candidates = [candidate(0, "a", 0), candidate(0, "a", 1), candidate(0, "a", 2),
                      candidate(1, "b", 0), candidate(2, "c", 0), candidate(2, "c", 1)]
        selected = select_diverse(candidates)
        self.assertEqual(selected, [candidates[index] for index in (0, 3, 4, 1, 5)])
        self.assertTrue(all(sum(item[1] == family for item in selected) <= 2 for family in ("a", "b", "c")))

    def test_total_cap_can_stop_during_second_round(self):
        candidates = [candidate(rank, str(rank), index)
                      for rank in range(3) for index in range(3)]
        self.assertEqual(select_diverse(candidates, max_total=4),
                         [candidates[index] for index in (0, 3, 6, 1)])

    def test_accepts_generator_and_preserves_original_tuple_with_extra_fields(self):
        first = candidate(0, "a", 0) + ({"metadata": "untouched"},)
        second = candidate(1, "b", 0)
        selected = select_diverse(iter((first, second)))
        self.assertIs(selected[0], first)
        self.assertIs(selected[1], second)

    def test_empty_and_disabled_limits(self):
        self.assertEqual(select_diverse([]), [])
        for total, per_family in ((0, 2), (-1, 2), (16, 0), (16, -1)):
            with self.subTest(total=total, per_family=per_family):
                self.assertEqual(select_diverse([candidate(0, "a", 0)], total, per_family), [])

    def test_custom_family_limit(self):
        candidates = [candidate(rank, str(rank), index)
                      for rank in range(2) for index in range(5)]
        selected = select_diverse(candidates, max_per_family=3)
        self.assertEqual(selected, [candidates[index] for index in (0, 5, 1, 6, 2, 7)])


if __name__ == "__main__":
    unittest.main()
