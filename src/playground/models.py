"""Small reviewed prediction recipe. No generated code executes in this process."""
from __future__ import annotations
import math
import re


class MeasurementUnavailable(ValueError):
    """Requested objects cannot support a numerical outcome measurement."""
    def __init__(self, object_ids, reason):
        self.object_ids = tuple(object_ids)
        self.reason = str(reason)
        super().__init__("Object measurement unavailable for " + ", ".join(self.object_ids) + ": " + self.reason)


def angle_difference(a, b):
    return (a - b + math.pi) % (2 * math.pi) - math.pi


def checked_prediction(value):
    if not isinstance(value, dict):
        raise ValueError("Prediction must be an object")
    result = {}
    for key in ("x", "y", "heading"):
        number = value[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or abs(number) > 1000 or not math.isfinite(number):
            raise ValueError("Predicted pose must contain finite numbers within absolute 1000")
        result[key] = float(number)
    uncertainty = value.get("uncertainty_m", 0.05)
    if isinstance(uncertainty, bool) or not isinstance(uncertainty, (int, float)) or not 0 <= uncertainty <= 2 or not math.isfinite(uncertainty):
        raise ValueError("uncertainty_m must be 0..2")
    result["uncertainty_m"] = float(uncertainty)
    if "objects" in value:
        objects = value["objects"]
        if not isinstance(objects, list) or len(objects) > 10:
            raise ValueError("Object predictions must be a list of at most 10 entries")
        checked, seen = [], set()
        for item in objects:
            if not isinstance(item, dict) or set(item) != {"id", "x", "y"}:
                raise ValueError("Object prediction accepts exactly id, x and y")
            identifier = item["id"]
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", identifier):
                raise ValueError("Object ID must be 1 to 64 letters, digits or identifier punctuation")
            if identifier in seen:
                raise ValueError("Duplicate predicted object ID")
            seen.add(identifier)
            coordinates = {}
            for key in ("x", "y"):
                number = item[key]
                if isinstance(number, bool) or not isinstance(number, (int, float)) or abs(number) > 1000 or not math.isfinite(number):
                    raise ValueError("Object position must contain finite numbers within absolute 1000")
                coordinates[key] = float(number)
            checked.append({"id": identifier, **coordinates})
        result["objects"] = checked
    return result


def prediction_error(prediction, observation):
    return prediction_error_breakdown(prediction, observation)["total_m_equivalent"]


def prediction_error_breakdown(prediction, observation):
    objects = checked_prediction(prediction).get("objects", [])
    measured = observation["robot"]
    robot_squared = ((prediction["x"] - measured["x"]) ** 2 + (prediction["y"] - measured["y"]) ** 2 +
                     (0.16 * angle_difference(prediction["heading"], measured["heading"])) ** 2)
    robot_error = math.sqrt(robot_squared)
    object_errors = {}
    object_squared = []
    if objects:
        tracked = observation.get("objects")
        if not isinstance(tracked, list):
            raise MeasurementUnavailable([item["id"] for item in objects], "tracked object list is unavailable")
        unavailable, reasons = [], []
        for predicted in objects:
            identifier = predicted["id"]
            matches = [item for item in tracked if isinstance(item, dict) and item.get("id") == identifier]
            if len(matches) != 1:
                unavailable.append(identifier)
                reasons.append("missing tracking" if not matches else "ambiguous duplicate tracking")
                continue
            actual = matches[0]
            coordinates = [actual.get(key) for key in ("x", "y")]
            if any(isinstance(number, bool) or not isinstance(number, (int, float)) or abs(number) > 1000 or not math.isfinite(number)
                   for number in coordinates):
                unavailable.append(identifier)
                reasons.append("invalid tracked position")
                continue
            squared = (predicted["x"] - actual["x"])**2 + (predicted["y"] - actual["y"])**2
            object_squared.append(squared)
            object_errors[identifier] = math.sqrt(squared)
        if unavailable:
            raise MeasurementUnavailable(unavailable, "; ".join(dict.fromkeys(reasons)))
    total = math.sqrt(robot_squared + sum(object_squared)) if object_squared else robot_error
    return {"robot_m_equivalent": robot_error, "objects_m": object_errors, "total_m_equivalent": total}


class MotionModel:
    def __init__(self, linear_mps=0.1, angular_rps=0.1 / 0.154, uncertainty_m=0.05):
        self.linear_mps = float(linear_mps)
        self.angular_rps = float(angular_rps)
        self.uncertainty_m = float(uncertainty_m)
        if not all(math.isfinite(x) for x in (self.linear_mps, self.angular_rps, self.uncertainty_m)):
            raise ValueError("Model coefficients must be finite")

    def predict(self, observation, action):
        p = observation["robot"]
        duration = action["duration_s"]
        linear = (action["left"] + action["right"]) / 2 * duration * self.linear_mps
        turn = (action["right"] - action["left"]) * duration * self.angular_rps
        # Exact differential-drive arc, with an explicit straight-line limit.
        if abs(turn) < 1e-9:
            dx, dy = linear * math.cos(p["heading"]), linear * math.sin(p["heading"])
        else:
            dx = linear / turn * (math.sin(p["heading"] + turn) - math.sin(p["heading"]))
            dy = linear / turn * (math.cos(p["heading"]) - math.cos(p["heading"] + turn))
        return {"x": p["x"] + dx, "y": p["y"] + dy, "heading": angle_difference(p["heading"] + turn, 0), "uncertainty_m": self.uncertainty_m}

    def to_dict(self):
        return {"recipe": "differential_drive_v1", "linear_mps": self.linear_mps, "angular_rps": self.angular_rps, "uncertainty_m": self.uncertainty_m}

    @classmethod
    def fit(cls, experiments):
        linear_terms, angular_terms = [], []
        for row in experiments:
            if row.get("phase") != "exploration" or row.get("status") != "completed":
                continue
            start, end, action = row["before"]["robot"], row["after"]["robot"], row["action"]
            turn = angle_difference(end["heading"], start["heading"])
            linear_feature = (action["left"] + action["right"]) / 2 * action["duration_s"]
            angular_feature = (action["right"] - action["left"]) * action["duration_s"]
            if abs(angular_feature) > 1e-6:
                angular_terms.append((angular_feature, turn))
            if abs(linear_feature) > 1e-6:
                midpoint = start["heading"] + turn / 2
                projected = (end["x"] - start["x"]) * math.cos(midpoint) + (end["y"] - start["y"]) * math.sin(midpoint)
                correction = math.sin(turn / 2) / (turn / 2) if abs(turn) > 1e-6 else 1
                linear_terms.append((linear_feature * correction, projected))
        if len(linear_terms) < 3 or len(angular_terms) < 3:
            raise ValueError("Need at least three translating and three turning exploration trials")
        def slope(terms):
            return sum(x * y for x, y in terms) / sum(x * x for x, _ in terms)
        model = cls(slope(linear_terms), slope(angular_terms))
        if not 0 < model.linear_mps <= 1 or not 0 < model.angular_rps <= 20:
            raise ValueError("Fit outside reviewed coefficient limits")
        errors = [prediction_error(model.predict(row["before"], row["action"]), row["after"]) for row in experiments if row.get("phase") == "exploration"]
        model.uncertainty_m = max(0.002, sorted(errors)[min(len(errors) - 1, int(len(errors) * .9))])
        return model
