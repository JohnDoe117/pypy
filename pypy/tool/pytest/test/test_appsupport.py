

def app_test_raises():
    info = raises(TypeError, id)
    assert info[0] is TypeError
    assert isinstance(info[1], TypeError)

    x = 43
    raises(ZeroDivisionError, "x/0")
