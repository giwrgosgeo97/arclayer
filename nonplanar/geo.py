"""Shape operations that let other threads run meanwhile.

Shapely's a.intersection(b) keeps Python locked while GEOS works; its array version doesn't. Same GEOS
call, same result: used where layers are prepared on several cores at once (see slicer.py)."""
import numpy as np
import shapely


def _one(f, a, b):
    x, y = np.empty(1, object), np.empty(1, object)
    x[0], y[0] = a, b
    return f(x, y)[0]


def inter(a, b):
    return _one(shapely.intersection, a, b)


def diff(a, b):
    return _one(shapely.difference, a, b)


def symdiff(a, b):
    return _one(shapely.symmetric_difference, a, b)


def simplify(a, tol):
    x = np.empty(1, object)
    x[0] = a
    return shapely.simplify(x, tol, preserve_topology=True)[0]
