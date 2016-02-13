from rpython.rlib.heapprof import *

class Value(object):
    pass

class OtherValue(Value):
    pass

class ValueInt(Value):
    def __init__(self, val):
        self.intval = val


class HeapProf(HeapProf):
    def is_int(self, val):
        return isinstance(val, ValueInt)

    def get_int_val(self, val):
        return val.intval


def test_int():
    v = HeapProf()
    assert v._hprof_status == SEEN_NOTHING
    v.see_write(ValueInt(1))
    v.see_write(ValueInt(1))
    v.see_write(ValueInt(1))
    v.see_write(ValueInt(1))
    assert v.read_constant_int() == 1
    assert v._hprof_status == SEEN_CONSTANT_INT
    v.see_write(ValueInt(2))
    assert v._hprof_status == SEEN_CONSTANT_CLASS
    assert v._hprof_const_cls is ValueInt
    v.see_write(ValueInt(1))
    assert v._hprof_status == SEEN_CONSTANT_CLASS
    assert v._hprof_const_cls is ValueInt
    v.see_write(ValueInt(2))
    assert v._hprof_status == SEEN_CONSTANT_CLASS
    assert v._hprof_const_cls is ValueInt
    v.see_write(ValueInt(3))
    assert v._hprof_status == SEEN_CONSTANT_CLASS
    assert v._hprof_const_cls is ValueInt

    v = HeapProf()
    assert v._hprof_status == SEEN_NOTHING
    v.see_write(ValueInt(1))
    v.see_write(Value())
    assert v._hprof_status == SEEN_TOO_MUCH
    v.see_write(Value())
    assert v._hprof_status == SEEN_TOO_MUCH


def test_obj():
    v = HeapProf()
    value = Value()
    assert v._hprof_status == SEEN_NOTHING
    v.see_write(value)
    v.see_write(value)
    v.see_write(value)
    v.see_write(value)
    assert v.try_read_constant_obj() is value
    assert v._hprof_status == SEEN_CONSTANT_OBJ
    v.see_write(ValueInt(2))
    assert v._hprof_status == SEEN_TOO_MUCH

    v = HeapProf()
    assert v._hprof_status == SEEN_NOTHING
    v.see_write(Value())
    v.see_write(OtherValue())
    assert v._hprof_status == SEEN_TOO_MUCH


def test_none():
    v = HeapProf()
    assert v._hprof_status == SEEN_NOTHING
    v.see_write(None)
    assert v._hprof_status == SEEN_TOO_MUCH
    v.see_write(None)
    assert v._hprof_status == SEEN_TOO_MUCH

    v = HeapProf()
    v.see_write(ValueInt(1))
    assert v._hprof_status == SEEN_CONSTANT_INT
    v.see_write(None)
    assert v._hprof_status == SEEN_TOO_MUCH

    v = HeapProf()
    v.see_write(Value())
    assert v._hprof_status == SEEN_CONSTANT_OBJ
    v.see_write(None)
    assert v._hprof_status == SEEN_TOO_MUCH

def test_known_class():
    import gc

    v = HeapProf()
    value = Value()
    assert v._hprof_status == SEEN_NOTHING
    v.see_write(value)
    assert v._hprof_status == SEEN_CONSTANT_OBJ
    v.see_write(Value())
    assert v._hprof_status == SEEN_CONSTANT_CLASS
    v.see_write(OtherValue())
    assert v._hprof_status == SEEN_TOO_MUCH

    v = HeapProf()
    assert v._hprof_status == SEEN_NOTHING
    v.see_write(value)
    assert v._hprof_status == SEEN_CONSTANT_OBJ
    v.see_write(Value())
    assert v._hprof_status == SEEN_CONSTANT_CLASS
    v.see_write(ValueInt(5))
    assert v._hprof_status == SEEN_TOO_MUCH

    v = HeapProf()
    assert v._hprof_status == SEEN_NOTHING
    v.see_write(Value())
    assert v._hprof_status == SEEN_CONSTANT_OBJ
    gc.collect()
    gc.collect()
    gc.collect()
    v.see_write(Value())
    assert v._hprof_status == SEEN_CONSTANT_CLASS
    v.see_write(OtherValue())
    assert v._hprof_status == SEEN_TOO_MUCH

def test_write_necessary_int():
    v = HeapProf()
    assert v._hprof_status == SEEN_NOTHING
    v.see_write(ValueInt(1))
    res = v.write_necessary(ValueInt(1))
    assert not res
    v.see_write(ValueInt(1))
    res = v.write_necessary(ValueInt(1))
    assert not res
    res = v.see_write(ValueInt(2))
    res = v.write_necessary(ValueInt(1))
    assert res
    res = v.see_write(ValueInt(2))
    res = v.write_necessary(ValueInt(1))
    assert res
    res = v.see_write(Value())
    res = v.write_necessary(ValueInt(1))
    assert res

    v = HeapProf()
    assert v._hprof_status == SEEN_NOTHING
    v.see_write(ValueInt(1))
    res = v.write_necessary(Value())
    assert res


def test_write_not_necessary_obj():
    v = HeapProf()
    assert v._hprof_status == SEEN_NOTHING
    val = Value()
    v.see_write(val)
    res = v.write_necessary(val)
    assert not res
    v.see_write(val)
    res = v.write_necessary(val)
    assert not res
    v.see_write(ValueInt(1))
    res = v.write_necessary(ValueInt(1))
    assert res
    v.see_write(Value())
    res = v.write_necessary(Value())
    assert res
