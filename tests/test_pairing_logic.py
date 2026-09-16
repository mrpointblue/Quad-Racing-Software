import unittest
from types import SimpleNamespace

from backend.pairing_logic import create_outer_pairs_by_start_number


def participant(participant_id: int, start_number: int):
    return SimpleNamespace(id=participant_id, start_number=start_number)


def pair_start_numbers(pairs):
    return [
        (
            driver_1.start_number,
            driver_2.start_number if driver_2 is not None else None,
        )
        for driver_1, driver_2 in pairs
    ]


class PreliminaryPairingTests(unittest.TestCase):
    def test_pairs_smallest_with_largest_start_number(self):
        participants = [
            participant(8, 8),
            participant(3, 3),
            participant(1, 1),
            participant(6, 6),
            participant(4, 4),
            participant(7, 7),
            participant(2, 2),
            participant(5, 5),
        ]

        pairs = create_outer_pairs_by_start_number(participants)

        self.assertEqual(
            pair_start_numbers(pairs),
            [(1, 8), (2, 7), (3, 6), (4, 5)],
        )

    def test_sorts_multi_digit_start_numbers_numerically(self):
        participants = [
            participant(10, 10),
            participant(2, 2),
            participant(11, 11),
            participant(1, 1),
        ]

        pairs = create_outer_pairs_by_start_number(participants)

        self.assertEqual(
            pair_start_numbers(pairs),
            [(1, 11), (2, 10)],
        )

    def test_odd_field_gives_middle_start_number_the_bye(self):
        participants = [
            participant(5, 50),
            participant(1, 10),
            participant(4, 40),
            participant(2, 20),
            participant(3, 30),
        ]

        pairs = create_outer_pairs_by_start_number(participants)

        self.assertEqual(
            pair_start_numbers(pairs),
            [(10, 50), (20, 40), (30, None)],
        )


if __name__ == "__main__":
    unittest.main()
