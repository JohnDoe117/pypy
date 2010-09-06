

class AppTestReferents(object):

    def setup_class(cls):
        from pypy.rlib import rgc
        cls._backup = [rgc.get_rpy_roots]
        w = cls.space.wrap
        cls.ALL_ROOTS = [w(4), w([2, 7])]
        cls.w_ALL_ROOTS = cls.space.newlist(cls.ALL_ROOTS)
        rgc.get_rpy_roots = lambda: map(rgc._GcRef, cls.ALL_ROOTS)

    def teardown_class(cls):
        from pypy.rlib import rgc
        rgc.get_rpy_roots = cls._backup[0]

    def test_get_objects(self):
        import gc
        lst = gc.get_objects()
        assert 2 in lst
        assert 4 in lst
        assert 7 in lst
        assert [2, 7] in lst
        for x in lst:
            if type(x) is gc.GcRef:
                assert 0, "get_objects() returned a GcRef"

    def test_get_rpy_referents(self):
        import gc
        y = 12345
        x = [y]
        lst = gc.get_rpy_referents(x)
        # After translation, 'lst' should contain the RPython-level list
        # (as a GcStruct).  Before translation, the 'wrappeditems' list.
        print lst
        lst2 = [x for x in lst if type(x) is gc.GcRef]
        assert lst2 != []
        # In any case, we should land on 'y' after one or two extra levels
        # of indirection.
        lst3 = []
        for x in lst2: lst3 += gc.get_rpy_referents(x)
        if y not in lst3:
            lst4 = []
            for x in lst3: lst4 += gc.get_rpy_referents(x)
            if y not in lst4:
                assert 0, "does not seem to reach 'y'"

    def test_get_rpy_memory_usage(self):
        import gc
        n = gc.get_rpy_memory_usage(12345)
        print n
        assert 4 <= n <= 64

    def test_get_referents(self):
        import gc
        y = 12345
        z = 23456
        x = [y, z]
        lst = gc.get_referents(x)
        assert y in lst and z in lst
