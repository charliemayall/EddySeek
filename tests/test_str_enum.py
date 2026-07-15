from eddy_seek.common import StrEnum


class Sample(StrEnum):
    HELLO = "world"


def test_str_enum_formats_as_value():
    assert f"hello from: {Sample.HELLO}" == "hello from: world"
    assert str(Sample.HELLO) == "world"
    assert Sample.HELLO == "world"
    assert isinstance(Sample.HELLO, str)
