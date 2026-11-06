"""Patient-native window statistics with no arithmetic contribution from outside a window.

Equal-time rows retain their normalized input order, matching the historical feature
contract. Do not replace these reductions by batch-prefix subtraction: batch, query
chunk and irrelevant/future rows must not affect the result for a fixed window.
"""
import numpy as np


class WindowIndex:
    def __init__(self, stay_idx, t_hours, values):
        stay, times, values = (np.asarray(stay_idx, np.int64), np.asarray(t_hours, np.float64),
                               np.asarray(values, np.float64))
        if stay.ndim != 1 or stay.shape != times.shape or times.shape != values.shape:
            raise ValueError('Event arrays must be aligned one-dimensional arrays')
        keep = np.isfinite(times) & np.isfinite(values)
        order = np.lexsort((np.flatnonzero(keep), times[keep], stay[keep]))
        self.stay, self.t, self.v = stay[keep][order], times[keep][order], values[keep][order]
        unique, starts, counts = np.unique(self.stay, return_index=True, return_counts=True)
        self.segments = {int(s): (int(a), int(a+n)) for s, a, n in zip(unique, starts, counts)}
        self.v_sentinel = np.append(self.v, np.nan)

    def bounds(self, q_stay, q_t, window=None, since_hour=None, include_t=True, closed_left=False):
        qs = np.asarray(q_stay, np.int64)
        qt = np.broadcast_to(np.asarray(q_t, np.float64), qs.shape)
        if qs.ndim != 1 or not np.isfinite(qt).all():
            raise ValueError('Query arrays must have finite aligned times')
        if window is not None and (not np.isfinite(window) or window < 0):
            raise ValueError('Window must be finite and nonnegative')
        lo = np.zeros(len(qs), np.int64); hi = lo.copy()
        for stay in np.unique(qs):
            if int(stay) not in self.segments:
                continue
            a, b = self.segments[int(stay)]; rows = np.flatnonzero(qs == stay); times = self.t[a:b]
            hi[rows] = a + np.searchsorted(times, qt[rows], side='right' if include_t else 'left')
            if since_hour is not None:
                lower = np.broadcast_to(np.asarray(since_hour, float), qs.shape)[rows]
                lo[rows] = a + np.searchsorted(times, lower, side='left')
            elif window is None:
                lo[rows] = a
            else:
                lo[rows] = a + np.searchsorted(times, qt[rows]-window, side='left' if closed_left else 'right')
        return lo, np.maximum(lo, hi)

    def count(self, lo, hi):
        return (hi-lo).astype(np.float64)

    def sum(self, lo, hi):
        return np.array([np.sum(self.v[a:b], dtype=np.float64) for a, b in zip(lo, hi)])

    def mean(self, lo, hi):
        return np.array([np.mean(self.v[a:b], dtype=np.float64) if b>a else np.nan for a, b in zip(lo, hi)])

    def std(self, lo, hi):
        output = np.full(len(lo), np.nan)
        for i, (a, b) in enumerate(zip(lo, hi)):
            if b-a < 2:
                continue
            centered = self.v[a:b]-self.v[a]
            centered -= np.mean(centered)
            output[i] = np.sqrt(np.dot(centered, centered)/(b-a-1))
        return output

    def slope(self, lo, hi, min_span_hours=1.):
        output = np.full(len(lo), np.nan)
        for i, (a, b) in enumerate(zip(lo, hi)):
            if b-a < 2 or self.t[b-1]-self.t[a] < min_span_hours:
                continue
            times = self.t[a:b]-self.t[a]; values = self.v[a:b]-self.v[a]
            times -= np.mean(times); values -= np.mean(values)
            denominator = np.dot(times, times)
            if (b-a)*denominator > 1e-9:
                output[i] = np.dot(times, values)/denominator
        return output

    def _reduce(self, ufunc, lo, hi):
        if not len(lo):
            return np.array([], np.float64)
        indices = np.empty(2*len(lo), np.int64); indices[::2] = lo; indices[1::2] = hi
        values = ufunc.reduceat(self.v_sentinel, indices)[::2]
        return np.where(hi>lo, values, np.nan)

    def min(self, lo, hi):
        return self._reduce(np.minimum, lo, hi)

    def max(self, lo, hi):
        return self._reduce(np.maximum, lo, hi)

    def last(self, q_stay, q_t, back=1):
        if back < 1 or int(back) != back:
            raise ValueError('back must be a positive integer')
        lo, hi = self.bounds(q_stay, q_t); positions = hi-back; ok = positions>=lo
        safe = np.where(ok, positions, 0)
        value = self.v[safe] if len(self.v) else np.full(len(safe), np.nan)
        time = self.t[safe] if len(self.t) else np.full(len(safe), np.nan)
        return np.where(ok, value, np.nan), np.where(ok, time, np.nan)
