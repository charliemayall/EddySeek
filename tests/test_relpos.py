"""
EddySeek - Offset vs Position algebra.
"""

from _eddy_seek.common import Offset, Position


def test_position_plus_offset():
    assert Position(10.0, 20.0) + Offset(1.5, -0.5) == Position(11.5, 19.5)


def test_position_minus_position_is_offset():
    assert Position(12.0, 18.0) - Position(10.0, 20.0) == Offset(2.0, -2.0)


def test_position_minus_offset():
    assert Position(11.5, 19.5) - Offset(1.5, -0.5) == Position(10.0, 20.0)


def test_offset_arithmetic():
    a = Offset(1.0, 2.0)
    b = Offset(0.5, -1.0)
    assert a + b == Offset(1.5, 1.0)
    assert a - b == Offset(0.5, 3.0)


def test_position_plus_position_rejected():
    try:
        Position(1.0, 2.0) + Position(3.0, 4.0)  # type: ignore[operator]
    except TypeError:
        pass
    else:
        raise AssertionError("Position + Position should raise TypeError")
