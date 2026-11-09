"""Small replaceable research baseline. Fit/calibration use training patients only."""
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.preprocessing import StandardScaler

from .models import FamilyPipeline, PlattCalibrator


class BaselinePreprocessor:
    """Adapter for FamilyPipeline; fitted independently inside each grouped fold."""
    def fit(self, X):
        self.imputer = SimpleImputer(strategy="median", keep_empty_features=True)
        self.scaler = StandardScaler().fit(self.imputer.fit_transform(X))
        return self

    def transform(self, X, rows=None):
        return self.scaler.transform(self.imputer.transform(X if rows is None else X[rows]))


def fit_classifier(X, labels, splits, patient_ids, feature_names, folds=3, seed=20260929):
    """Return FamilyPipeline and aggregate audit; labels maps horizon -> nullable array.

    No validation or test labels are used for preprocessing, calibration, or fitting.
    Platt calibration sees only patient-grouped out-of-fold training probabilities.
    """
    X, splits, patient_ids = np.asarray(X, float), np.asarray(splits), np.asarray(patient_ids)
    if X.ndim != 2 or len(feature_names) != X.shape[1] or len(splits) != len(X):
        raise ValueError("Feature/identity shape mismatch")
    if np.isinf(X).any():
        raise ValueError("Features may be missing but not infinite")
    pipe = FamilyPipeline("lr", "extension_baseline", feature_names, sorted(labels))
    audit = {"fit_split": "train", "calibration": "patient-grouped train OOF", "folds": folds,
             "validation_used_for_fit": False, "test_used": False, "horizons": {}}
    for h, y in labels.items():
        y = np.asarray(y, float)
        rows = np.flatnonzero((splits == "train") & np.isfinite(y))
        xx, yy, groups = X[rows], y[rows].astype(int), patient_ids[rows]
        if set(np.unique(yy)) != {0, 1} or len(np.unique(groups)) < folds:
            raise ValueError(f"Insufficient labelled training patients/classes for horizon {h}")
        oof = np.full(len(rows), np.nan)
        cv = StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=seed)
        for tr, va in cv.split(xx, yy, groups):
            if set(np.unique(yy[tr])) != {0, 1}:
                raise ValueError("A training fold lacks a class; change the development protocol explicitly")
            assert not set(groups[tr]) & set(groups[va])
            pre = BaselinePreprocessor().fit(xx[tr])
            head = LogisticRegression(C=1.0, max_iter=500, random_state=seed)
            head.fit(pre.transform(xx[tr]), yy[tr])
            oof[va] = head.predict_proba(pre.transform(xx[va]))[:, 1]
        if not np.isfinite(oof).all():
            raise ValueError("Incomplete grouped OOF predictions")
        pipe.calibrators[h] = {"platt": PlattCalibrator().fit(oof, yy)}
        pre = BaselinePreprocessor().fit(xx)
        pipe.preprocessors[h] = pre
        pipe.heads[h] = LogisticRegression(C=1.0, max_iter=500, random_state=seed).fit(pre.transform(xx), yy)
        audit["horizons"][str(h)] = {"rows": len(rows), "patients": len(np.unique(groups))}
    return pipe, audit
