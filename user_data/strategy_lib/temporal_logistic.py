"""Small deterministic NumPy logistic model for frozen walk-forward research."""
from __future__ import annotations

import math

import numpy as np


def fit_logistic_regression(
    features: np.ndarray,
    labels: np.ndarray,
    *,
    l2: float = 0.1,
    learning_rate: float = 0.1,
    steps: int = 500,
) -> dict[str, object]:
    x = np.asarray(features, dtype=float)
    y = np.asarray(labels, dtype=float)
    if (x.ndim != 2 or len(x) == 0 or y.shape != (len(x),)
            or not np.isfinite(x).all() or not np.isfinite(y).all()
            or not np.isin(y, (0.0, 1.0)).all()):
        raise ValueError("features and binary labels must be nonempty, finite and aligned")
    if (isinstance(l2, bool) or not math.isfinite(l2) or l2 < 0
            or isinstance(learning_rate, bool) or not math.isfinite(learning_rate)
            or learning_rate <= 0 or type(steps) is not int or steps < 1):
        raise ValueError("invalid logistic training parameters")
    if np.unique(y).size != 2:
        raise ValueError("logistic training requires both label classes")

    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-12] = 1.0
    standardized = (x - mean) / scale
    design = np.column_stack((np.ones(len(standardized)), standardized))
    coefficients = np.zeros(design.shape[1], dtype=float)
    regularization = np.r_[0.0, np.full(standardized.shape[1], l2)]
    for _ in range(steps):
        logits = np.clip(design @ coefficients, -30.0, 30.0)
        probabilities = 1.0 / (1.0 + np.exp(-logits))
        gradient = design.T @ (probabilities - y) / len(y)
        gradient += regularization * coefficients
        coefficients -= learning_rate * gradient
    if not np.isfinite(coefficients).all():
        raise FloatingPointError("logistic fit produced non-finite coefficients")
    return {
        "feature_mean": mean.tolist(),
        "feature_scale": scale.tolist(),
        "coefficients": coefficients.tolist(),
        "l2": float(l2),
        "learning_rate": float(learning_rate),
        "steps": steps,
        "training_rows": int(len(y)),
        "positive_rate": float(y.mean()),
    }


def predict_probability(model: dict[str, object], features: np.ndarray) -> np.ndarray:
    x = np.asarray(features, dtype=float)
    mean = np.asarray(model["feature_mean"], dtype=float)
    scale = np.asarray(model["feature_scale"], dtype=float)
    coefficients = np.asarray(model["coefficients"], dtype=float)
    if (x.ndim != 2 or x.shape[1] != len(mean) or len(scale) != len(mean)
            or len(coefficients) != len(mean) + 1 or not np.isfinite(x).all()
            or not np.isfinite(mean).all() or not np.isfinite(scale).all()
            or not np.isfinite(coefficients).all() or (scale <= 0).any()):
        raise ValueError("model and feature dimensions or values are invalid")
    standardized = (x - mean) / scale
    design = np.column_stack((np.ones(len(standardized)), standardized))
    logits = np.clip(design @ coefficients, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-logits))
