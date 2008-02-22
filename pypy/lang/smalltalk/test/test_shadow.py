import random
from pypy.lang.smalltalk import model, shadow, classtable, constants, objtable
from pypy.lang.smalltalk import utility

w_Object = classtable.classtable['w_Object']
w_Metaclass  = classtable.classtable['w_Metaclass']
w_MethodDict = classtable.classtable['w_MethodDict']
w_Array      = classtable.classtable['w_Array']

def build_methoddict(methods):
    size = int(len(methods) * 1.5)
    w_methoddict = w_MethodDict.as_class_get_shadow().new(size)
    w_array = w_Array.as_class_get_shadow().new(size)
    for i in range(size):
        w_array.store(i, objtable.w_nil)
        w_methoddict.store(constants.METHODDICT_NAMES_INDEX+i, objtable.w_nil)
    w_tally = utility.wrap_int(len(methods))
    w_methoddict.store(constants.METHODDICT_TALLY_INDEX, w_tally)
    w_methoddict.store(constants.METHODDICT_VALUES_INDEX, w_array)
    positions = range(size)
    random.shuffle(positions)
    for selector, w_compiledmethod in methods.items():
        pos = positions.pop()
        w_selector = utility.wrap_string(selector)
        w_methoddict.store(constants.METHODDICT_NAMES_INDEX+pos, w_selector)
        w_array.store(pos, w_compiledmethod)
    return w_methoddict

def build_smalltalk_class(name, format, w_superclass=w_Object,
                          w_classofclass=None, methods={}):
    if w_classofclass is None:
        w_classofclass = build_smalltalk_class(None, 0x94,
                                               w_superclass.w_class,
                                               w_Metaclass)
    w_methoddict = build_methoddict(methods)
    size = constants.CLASS_NAME_INDEX + 1
    w_class = model.W_PointersObject(w_classofclass, size)
    w_class.store(constants.CLASS_SUPERCLASS_INDEX, w_superclass)
    w_class.store(constants.CLASS_METHODDICT_INDEX, w_methoddict)
    w_class.store(constants.CLASS_FORMAT_INDEX, utility.wrap_int(format))
    if name is not None:
        w_class.store(constants.CLASS_NAME_INDEX, utility.wrap_string(name))
    return w_class

def basicshape(name, format, kind, varsized, instsize):
    w_class = build_smalltalk_class(name, format)
    classshadow = w_class.as_class_get_shadow()
    assert classshadow.instance_kind == kind
    assert classshadow.isvariable() == varsized
    assert classshadow.instsize() == instsize
    assert classshadow.name == name
    assert classshadow.s_superclass is w_Object.as_class_get_shadow()

def test_basic_shape():
    yield basicshape, "Empty",        0x02,    shadow.POINTERS, False, 0
    yield basicshape, "Seven",        0x90,    shadow.POINTERS, False, 7
    yield basicshape, "Seventyseven", 0x1009C, shadow.POINTERS, False, 77
    yield basicshape, "EmptyVar",     0x102,   shadow.POINTERS, True,  0
    yield basicshape, "VarTwo",       0x3986,  shadow.POINTERS, True,  2
    yield basicshape, "VarSeven",     0x190,   shadow.POINTERS, True,  7
    yield basicshape, "Bytes",        0x402,   shadow.BYTES,    True,  0
    yield basicshape, "Words",        0x302,   shadow.WORDS,    True,  0
    yield basicshape, "CompiledMeth", 0xE02,   shadow.COMPILED_METHOD, True, 0

def test_methoddict():
    methods = {'foo': model.W_CompiledMethod(0),
               'bar': model.W_CompiledMethod(0)}
    w_class = build_smalltalk_class("Demo", 0x90, methods=methods)
    classshadow = w_class.as_class_get_shadow()
    assert classshadow.methoddict == methods

def link(w_next='foo'):
    w_object = model.W_PointersObject(None, 1)
    w_object.store(constants.NEXT_LINK_INDEX, w_next)
    return w_object

def test_link():
    w_object = link()
    assert w_object.as_link_get_shadow().next() == 'foo'

def method(tempsize=3,argsize=2, bytes="abcde"):
    w_m = model.W_CompiledMethod()
    w_m.bytes = bytes
    w_m.tempsize = tempsize
    w_m.argsize = argsize
    w_m.literalsize = 2
    return w_m

def methodcontext(w_sender=objtable.w_nil, pc=1, stackpointer=0, stacksize=5,
                  method=method()):
    stackstart = 7 # (len notation, not idx notation)
    w_object = model.W_PointersObject(classtable.w_MethodContext, stackstart+stacksize)
    w_object.store(constants.CTXPART_SENDER_INDEX, w_sender)
    w_object.store(constants.CTXPART_PC_INDEX, utility.wrap_int(pc))
    w_object.store(constants.CTXPART_STACKP_INDEX, utility.wrap_int(stackstart+stackpointer))
    w_object.store(constants.MTHDCTX_METHOD, method)
    # XXX
    w_object.store(constants.MTHDCTX_RECEIVER_MAP, '???')
    w_object.store(constants.MTHDCTX_RECEIVER, 'receiver')

    # XXX Might want to check the realness of the next assumption,
    # XXX made by hooking into the suspended thread of the image.
    # XXX it seems the only possibility, and using this assumption
    # XXX it actually runs...
    # Weirdly enough, undependant from the size of the tempsize and
    # argsize, the stackpointer can point to anything starting from
    # the temp_frame_start. That's why stacks always print all elements
    # including possible "temps or args"
    w_object.store(constants.MTHDCTX_TEMP_FRAME_START, 'el')
    return w_object

def test_context():
    w_m = method()
    w_object = methodcontext(stackpointer=2, method=w_m)
    w_object2 = methodcontext(w_sender=w_object)
    s_object = w_object.as_methodcontext_get_shadow()
    s_object2 = w_object2.as_methodcontext_get_shadow()
    assert w_object2.fetch(constants.CTXPART_SENDER_INDEX) == w_object
    assert s_object.w_self() == w_object
    assert s_object2.w_self() == w_object2
    assert s_object.s_sender() == None
    assert s_object2.s_sender() == s_object
    assert s_object.w_receiver() == 'receiver'
    s_object2.settemp(0, 'a')
    s_object2.settemp(1, 'b')
    assert s_object2.gettemp(1) == 'b'
    assert s_object2.gettemp(0) == 'a'
    assert s_object.w_method() == w_m
    idx = s_object.stackstart()
    w_object.store(idx + 1, 'f')
    w_object.store(idx + 2, 'g')
    w_object.store(idx + 3, 'h')
    assert s_object.top() == 'h'
    assert s_object.stack() == ['el', 'f', 'g', 'h' ]
    s_object.push('i')
    assert s_object.top() == 'i'
    assert s_object.peek(1) == 'h'
    assert s_object.pop() == 'i'
    assert s_object.pop_and_return_n(2) == ['g', 'h']
    assert s_object.pop() == 'f'
    assert s_object.stackpointer() == s_object.stackstart()

def test_methodcontext():
    w_m = method()
                              # Point over 2 literals of size 4
    w_object = methodcontext(pc=9,method=w_m)
    s_object = w_object.as_methodcontext_get_shadow()
    assert s_object.getbytecode() == 97
    assert s_object.getbytecode() == 98
    assert s_object.getbytecode() == 99
    assert s_object.getbytecode() == 100
    assert s_object.getbytecode() == 101
    assert s_object.s_home() == s_object

def process(w_context=methodcontext(), priority=utility.wrap_int(3)):
    w_object = model.W_PointersObject(None, 4)
    w_object.store(constants.NEXT_LINK_INDEX, 'foo')
    w_object.store(constants.PROCESS_SUSPENDED_CONTEXT_INDEX, w_context)
    w_object.store(constants.PROCESS_PRIORITY_INDEX, priority)
    w_object.store(constants.PROCESS_MY_LIST_INDEX, 'mli')
    return w_object

def test_process():
    w_context = methodcontext()
    w_object = process(w_context)
    s_object = w_object.as_process_get_shadow()
    assert s_object.next() == 'foo'
    assert s_object.priority() == 3
    assert s_object.my_list() == 'mli'
    assert s_object.s_suspended_context() == w_context.as_context_get_shadow()

def test_association():
    w_object = model.W_PointersObject(None, 2)
    w_object.store(constants.ASSOCIATION_KEY_INDEX, 'key')
    w_object.store(constants.ASSOCIATION_VALUE_INDEX, 'value')
    s_object = w_object.as_association_get_shadow()
    assert s_object.key() == 'key'
    assert s_object.value() == 'value'

def test_scheduler():
    w_process = process()
    w_object = model.W_PointersObject(None, 2)
    w_object.store(constants.SCHEDULER_ACTIVE_PROCESS_INDEX, w_process)
    w_object.store(constants.SCHEDULER_PROCESS_LISTS_INDEX, 'pl')
    s_object = w_object.as_scheduler_get_shadow()
    assert s_object.s_active_process() == w_process.as_process_get_shadow()
    assert s_object.process_lists() == 'pl'
    w_process2 = process()
    s_object.store_w_active_process(w_process2)
    assert s_object.process_lists() == 'pl'
    assert s_object.s_active_process() != w_process.as_process_get_shadow()
    assert s_object.s_active_process() == w_process2.as_process_get_shadow()

def test_linkedlist():
    w_object = model.W_PointersObject(None,2)
    w_last = link(objtable.w_nil)
    w_lb1 = link(w_last)
    w_lb2 = link(w_lb1)
    w_lb3 = link(w_lb2)
    w_lb4 = link(w_lb3)
    w_first = link(w_lb4)
    w_object.store(constants.FIRST_LINK_INDEX, w_first)
    w_object.store(constants.LAST_LINK_INDEX, w_last)
    s_object = w_object.as_linkedlist_get_shadow()
    assert w_first == s_object.w_firstlink()
    assert w_last == s_object.w_lastlink()
    assert s_object.remove_first_link_of_list() == w_first
    assert s_object.remove_first_link_of_list() == w_lb4
    assert s_object.remove_first_link_of_list() == w_lb3
    assert not s_object.is_empty_list()
    assert s_object.remove_first_link_of_list() == w_lb2
    assert s_object.remove_first_link_of_list() == w_lb1
    assert s_object.remove_first_link_of_list() == w_last
    assert s_object.is_empty_list()
    s_object.add_last_link(w_first)
    assert s_object.w_firstlink() == w_first
    assert s_object.w_lastlink() == w_first
    s_object.add_last_link(w_last)
    assert s_object.w_firstlink() == w_first
    assert s_object.w_lastlink() == w_last

def test_shadowchanges():
    w_object = model.W_PointersObject(None, 2)
    w_o1 = link('a')
    w_o2 = link('b')
    w_object.store(0, w_o1)
    w_object.store(1, w_o2)
    s_object = w_object.as_linkedlist_get_shadow()
    assert s_object.w_firstlink() == w_o1
    assert s_object.w_lastlink() == w_o2
    assert w_object._shadow == s_object
    s_object2 = w_object.as_association_get_shadow()
    assert s_object2.key() == w_o1
    assert s_object2.value() == w_o2
    assert w_object._shadow == s_object2
    s_object.check_for_updates()
    assert s_object.w_firstlink() == w_o1
    assert s_object.w_lastlink() == w_o2
    assert w_object._shadow == s_object
