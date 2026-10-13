import math
import random
import unittest

from playground.models import MeasurementUnavailable, MotionModel, angle_difference, checked_prediction, prediction_error, prediction_error_breakdown
from playground.research import simulation_controller


class PredictionTests(unittest.TestCase):
    def prediction(self, objects=None):
        value = {"x": .5, "y": .5, "heading": 0, "uncertainty_m": .05}
        if objects is not None: value["objects"] = objects
        return value

    def test_robot_metric_is_exactly_the_previous_formula(self):
        rng = random.Random(0)
        for _ in range(100):
            prediction = {"x": rng.random(), "y": rng.random(), "heading": rng.uniform(-math.pi, math.pi)}
            measured = {"x": rng.random(), "y": rng.random(), "heading": rng.uniform(-math.pi, math.pi)}
            expected = math.sqrt((prediction["x"]-measured["x"])**2 + (prediction["y"]-measured["y"])**2 +
                                 (.16*angle_difference(prediction["heading"], measured["heading"]))**2)
            self.assertEqual(prediction_error(prediction, {"robot": measured}), expected)
            prediction["objects"] = []
            self.assertEqual(prediction_error(prediction, {"robot": measured}), expected)

    def test_named_object_errors_match_ids_not_order(self):
        prediction = self.prediction([{"id": "a", "x": 3, "y": 4}, {"id": "7", "x": 0, "y": 2}])
        after = {"robot": {"x": .5, "y": .5, "heading": 0}, "objects": [
            {"id": "unrequested", "x": math.nan, "y": 0}, {"id": "7", "x": 0, "y": 0}, {"id": "a", "x": 0, "y": 0}]}
        breakdown = prediction_error_breakdown(prediction, after)
        self.assertEqual(breakdown["robot_m_equivalent"], 0)
        self.assertEqual(breakdown["objects_m"], {"a": 5, "7": 2})
        self.assertEqual(breakdown["total_m_equivalent"], math.sqrt(29))
        self.assertEqual(prediction_error(prediction, after), math.sqrt(29))

    def test_missing_ambiguous_and_nonfinite_tracking_is_unavailable(self):
        prediction = self.prediction([{"id": "block", "x": .6, "y": .5}])
        observations = [None, [], [{"id": "other", "x": .6, "y": .5}],
                        [{"id": "block", "x": .6, "y": .5}] * 2,
                        [{"id": "block", "x": math.nan, "y": .5}],
                        [{"id": "block", "x": 1e308, "y": .5}],
                        [{"id": "block", "x": 10**1000, "y": .5}],
                        [{"id": "block", "x": True, "y": .5}],
                        [{"id": "block", "x": .6}]]
        for objects in observations:
            with self.subTest(objects=objects), self.assertRaises(MeasurementUnavailable) as caught:
                prediction_error(prediction, {"robot": {"x": .5, "y": .5, "heading": 0}, "objects": objects})
            self.assertEqual(caught.exception.object_ids, ("block",))
            self.assertIn("unavailable", str(caught.exception))

    def test_object_prediction_structure_and_ids_are_bounded(self):
        invalid = [None, {}, [{"id": "block", "x": 0, "y": 0}] * 2,
                   [{"id": str(i), "x": 0, "y": 0} for i in range(11)],
                   [{"id": "block", "x": 0}], [{"id": "block", "x": 0, "y": 0, "held": True}]]
        for identifier in (None, 4, True, "", " ", "../block", "a/b", "a\n", "x" * 65):
            invalid.append([{"id": identifier, "x": 0, "y": 0}])
        for objects in invalid:
            value = self.prediction()
            value["objects"] = objects
            with self.subTest(objects=objects), self.assertRaises(ValueError):
                checked_prediction(value)
        valid = self.prediction([{"id": "marker:7", "x": 1, "y": 2}])
        self.assertEqual(checked_prediction(valid)["objects"], [{"id": "marker:7", "x": 1.0, "y": 2.0}])

    def test_nonfinite_or_nonnumeric_object_coordinates_rejected(self):
        for number in (math.nan, math.inf, -math.inf, True, "0.5", None, 1e308, 10**1000):
            for coordinate in ("x", "y"):
                item = {"id": "block", "x": .6, "y": .5}
                item[coordinate] = number
                with self.subTest(coordinate=coordinate, number=number), self.assertRaises(ValueError):
                    checked_prediction(self.prediction([item]))

    def test_robot_prediction_rejects_bool_strings_and_overflow_before_scoring(self):
        for key in ("x", "y", "heading"):
            for number in (True, "1", 1e308, 10**1000, math.nan, math.inf):
                value = self.prediction()
                value[key] = number
                with self.subTest(key=key, number=number), self.assertRaises(ValueError):
                    checked_prediction(value)

    def test_controlled_grasp_scores_object_effect_separately_from_robot_pose(self):
        controller, clock = simulation_controller(speed=.075)
        token = controller.acquire("object-test", ttl_s=30)["lease_id"]
        controller.arm(token, human_confirmed=True)
        def act(action):
            controller.action(token, **action)
            remaining = action["duration_s"]
            while remaining > 1e-9:
                step = min(.05, remaining)
                clock.advance(step)
                controller.heartbeat(token)
                controller.renew(token, ttl_s=30)
                remaining -= step
        try:
            for _ in range(2): act({"left": 1, "right": 1, "duration_s": .4})
            before = controller.observe()
            self.assertFalse(before["objects"][0]["held"])
            action = {"left": .6, "right": .6, "duration_s": .2, "gripper": 1}
            prediction = MotionModel(.075, .075/.154).predict(before, action)
            prediction["objects"] = [{"id": "block", "x": prediction["x"]+.06, "y": prediction["y"]}]
            act(action)
            after = controller.observe()
            self.assertTrue(after["objects"][0]["held"])
            correct = prediction_error_breakdown(prediction, after)
            self.assertLess(correct["robot_m_equivalent"], 1e-12)
            self.assertLess(correct["objects_m"]["block"], 1e-12)
            prediction["objects"][0]["x"] = before["objects"][0]["x"]
            wrong = prediction_error_breakdown(prediction, after)
            self.assertLess(wrong["robot_m_equivalent"], 1e-12)
            self.assertGreater(wrong["objects_m"]["block"], .01)
            self.assertGreater(wrong["total_m_equivalent"], .01)
        finally:
            controller.close()


if __name__ == "__main__":
    unittest.main()
