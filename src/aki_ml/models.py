"""Model-family pipeline, learned LR preprocessing, and calibrators.

Everything here applies fixed, already-fitted parameters; training logic lives in notebook 02.
All transforms keep float32 and work in row chunks so they fit in a few GB of RAM.
"""
import numpy as np
import pyarrow.parquet as pq
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import SplineTransformer

CHUNK_ROWS = 100_000


def logit(p, eps=1e-6):
    p = np.clip(np.asarray(p, dtype=np.float64), eps, 1 - eps)
    return np.log(p / (1 - p))


def _rows(X, rows):
    return np.arange(X.shape[0]) if rows is None else np.asarray(rows)


def load_feature_matrix(path, feature_names, filters=None, batch_columns=40):
    """Read features from a Parquet file into a C-ordered float32 matrix without a full pandas copy."""
    n = pq.read_table(path, columns=[feature_names[0]], filters=filters).num_rows
    X = np.empty((n, len(feature_names)), dtype=np.float32)
    for start in range(0, len(feature_names), batch_columns):
        cols = feature_names[start:start + batch_columns]
        table = pq.read_table(path, columns=cols, filters=filters)
        for j, c in enumerate(cols):
            X[:, start + j] = table.column(c).to_numpy().astype(np.float32, copy=False)
        del table
    return X


class LRPreprocessor:
    """Winsorize -> median impute (+ missing indicators) -> standardize, plus a B-spline basis for time.

    `cols` selects the model's feature columns of the shared matrix; `rows` selects the partition.
    """

    def __init__(self, feature_names, winsorize_quantiles=(0.005, 0.995), indicator_min_fraction=0.01,
                 time_feature="hours_since_icu", n_knots=5, degree=3, fit_sample_rows=300_000, seed=0):
        self.feature_names = list(feature_names)
        self.winsorize_quantiles = tuple(winsorize_quantiles)
        self.indicator_min_fraction = indicator_min_fraction
        self.time_feature = time_feature
        self.n_knots, self.degree = n_knots, degree
        self.fit_sample_rows, self.seed = fit_sample_rows, seed

    def fit(self, X, rows=None, cols=None):
        rows = _rows(X, rows)
        if len(rows) > self.fit_sample_rows:
            rng = np.random.default_rng(self.seed)
            rows = np.sort(rng.choice(rows, self.fit_sample_rows, replace=False))
        cols = np.arange(X.shape[1]) if cols is None else np.asarray(cols)
        self.cols_ = cols
        S = X[np.ix_(rows, cols)]
        missing = np.isnan(S)
        self.indicator_idx_ = np.flatnonzero(missing.mean(axis=0) >= self.indicator_min_fraction)
        q_lo, q_hi = self.winsorize_quantiles
        self.lo_, self.hi_ = (np.nanquantile(S, q, axis=0).astype(np.float32) for q in (q_lo, q_hi))
        np.clip(S, self.lo_, self.hi_, out=S)
        self.median_ = np.nan_to_num(np.nanmedian(S, axis=0), nan=0.0).astype(np.float32)
        S[missing] = np.take(self.median_, np.nonzero(missing)[1])
        self.mean_ = S.mean(axis=0, dtype=np.float64).astype(np.float32)
        std = S.std(axis=0, dtype=np.float64)
        self.scale_ = np.where(std > 1e-8, std, 1.0).astype(np.float32)
        t = self.feature_names.index(self.time_feature)
        self.time_col_ = t
        self.spline_ = SplineTransformer(n_knots=self.n_knots, degree=self.degree, extrapolation="constant",
                                         include_bias=False).fit(S[:, [t]].astype(np.float64))
        self.n_spline_ = self.spline_.transform(S[:1, [t]].astype(np.float64)).shape[1]
        return self

    @property
    def feature_names_out_(self):
        names = [self.feature_names[i] for i in range(len(self.cols_))]
        return (names + [f"{names[i]}__missing" for i in self.indicator_idx_]
                + [f"{self.time_feature}__spline{k}" for k in range(self.n_spline_)])

    @property
    def n_out_(self):
        return len(self.cols_) + len(self.indicator_idx_) + self.n_spline_

    def transform(self, X, rows=None):
        rows = _rows(X, rows)
        p, k = len(self.cols_), len(self.indicator_idx_)
        out = np.empty((len(rows), self.n_out_), dtype=np.float32)
        for s in range(0, len(rows), CHUNK_ROWS):
            r = rows[s:s + CHUNK_ROWS]
            C = X[np.ix_(r, self.cols_)]
            missing = np.isnan(C)
            out[s:s + len(r), p:p + k] = missing[:, self.indicator_idx_]
            np.clip(C, self.lo_, self.hi_, out=C)
            C[missing] = np.take(self.median_, np.nonzero(missing)[1])
            out[s:s + len(r), p + k:] = self.spline_.transform(C[:, [self.time_col_]].astype(np.float64))
            out[s:s + len(r), :p] = (C - self.mean_) / self.scale_
        return out


class PlattCalibrator:
    """Logistic recalibration on the logit of the raw score (primary calibration method)."""

    def __init__(self, eps=1e-6):
        self.eps = eps

    def fit(self, score, y):
        lr = LogisticRegression(penalty=None, solver="lbfgs", max_iter=1000)
        lr.fit(logit(score, self.eps).reshape(-1, 1), np.asarray(y).astype(int))
        self.slope_, self.intercept_ = float(lr.coef_[0, 0]), float(lr.intercept_[0])
        return self

    def predict(self, score):
        z = self.slope_ * logit(score, self.eps) + self.intercept_
        return (1.0 / (1.0 + np.exp(-z))).astype(np.float32)


class IsotonicCalibrator:
    """Monotone non-parametric recalibration (prespecified sensitivity analysis only)."""

    def fit(self, score, y):
        self.iso_ = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
        self.iso_.fit(np.asarray(score, dtype=np.float64), np.asarray(y, dtype=np.float64))
        return self

    def predict(self, score):
        return self.iso_.predict(np.asarray(score, dtype=np.float64)).astype(np.float32)


class FamilyPipeline:
    """One model family: shared feature order, optional preprocessing, and one head per horizon.

    heads[h]       fitted estimator (sklearn LogisticRegression / RandomForest, LightGBM / XGBoost Booster)
    n_rounds[h]    boosting rounds used at prediction time (boosted families only)
    calibrators[h] {"platt": PlattCalibrator, "isotonic": IsotonicCalibrator}
    preprocessors  {h: LRPreprocessor} for the LR family, else empty
    """

    def __init__(self, family, feature_set, feature_names, horizons, metadata=None):
        self.family, self.feature_set = family, feature_set
        self.feature_names = list(feature_names)
        self.horizons = list(horizons)
        self.heads, self.n_rounds, self.calibrators, self.preprocessors = {}, {}, {}, {}
        self.metadata = metadata or {}

    def matrix_from_frame(self, df):
        """Build the float32 input matrix in the exact training feature order."""
        missing = [c for c in self.feature_names if c not in df.columns]
        if missing:
            raise KeyError(f"missing {len(missing)} model features, e.g. {missing[:5]}")
        return np.ascontiguousarray(df[self.feature_names].to_numpy(dtype=np.float32))

    def raw_score(self, X, horizon, rows=None, cols=None):
        """Uncalibrated head output for rows of X (cols maps X columns to this pipeline's features)."""
        rows = _rows(X, rows)
        model = self.heads[horizon]
        if self.family == "lr":
            return model.predict_proba(self.preprocessors[horizon].transform(X, rows))[:, 1].astype(np.float32)
        out = np.empty(len(rows), dtype=np.float32)
        for s in range(0, len(rows), CHUNK_ROWS):
            r = rows[s:s + CHUNK_ROWS]
            Xc = X[r] if cols is None else X[np.ix_(r, cols)]
            if self.family == "rf":
                out[s:s + len(r)] = model.predict_proba(Xc)[:, 1]
            elif self.family == "lightgbm":
                out[s:s + len(r)] = model.predict(Xc, num_iteration=self.n_rounds[horizon])
            elif self.family == "xgboost":
                out[s:s + len(r)] = model.inplace_predict(Xc, iteration_range=(0, self.n_rounds[horizon]))
            else:
                raise ValueError(f"unknown family {self.family}")
        return out

    def predict(self, X, horizon, calibration="platt", rows=None, cols=None):
        score = self.raw_score(X, horizon, rows, cols)
        return score if calibration in (None, "none") else self.calibrators[horizon][calibration].predict(score)

    def predict_frame(self, df, calibration="platt"):
        """Risk for every horizon from a DataFrame holding the model features (dashboard/replay entry point)."""
        X = self.matrix_from_frame(df)
        return {h: self.predict(X, h, calibration) for h in self.horizons}
