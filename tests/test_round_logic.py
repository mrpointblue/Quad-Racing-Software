import unittest
from types import SimpleNamespace

from backend.round_logic import create_middle_out_pairs


def ranked_participant(place: int):
    return SimpleNamespace(
        id=place,
        place=place,
        start_number=place,
        active=True,
        withdrawn=False,
    )


def pair_places(pairs):
    return [
        (
            driver_1.place,
            driver_2.place if driver_2 is not None else None,
        )
        for driver_1, driver_2 in pairs
    ]


class MiddleOutPairingTests(unittest.TestCase):
    def test_even_field_starts_in_the_middle_and_moves_outward(self):
        participants = [ranked_participant(place) for place in range(1, 9)]

        pairs = create_middle_out_pairs(participants)

        self.assertEqual(
            pair_places(pairs),
            [(4, 5), (3, 6), (2, 7), (1, 8)],
        )

    def test_odd_field_leaves_last_place_as_single_driver(self):
        participants = [ranked_participant(place) for place in range(1, 10)]

        pairs = create_middle_out_pairs(participants)

        self.assertEqual(
            pair_places(pairs),
            [(4, 5), (3, 6), (2, 7), (1, 8), (9, None)],
        )

    def test_five_driver_field_uses_same_rule(self):
        participants = [ranked_participant(place) for place in range(1, 6)]

        pairs = create_middle_out_pairs(participants)

        self.assertEqual(
            pair_places(pairs),
            [(2, 3), (1, 4), (5, None)],
        )


if __name__ == "__main__":
    unittest.main()
