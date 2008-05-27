
import math
from pypy.lang.js.jsparser import parse, ParseError
from pypy.lang.js.astbuilder import ASTBuilder
from pypy.lang.js.jsobj import global_context, W_Object,\
     w_Undefined, W_NewBuiltin, W_IntNumber, w_Null, create_object, W_Boolean,\
     W_FloatNumber, W_String, W_Builtin, W_Array, w_Null,\
     isnull_or_undefined, W_PrimitiveObject, W_ListObject
from pypy.lang.js.execution import ThrowException, JsTypeError
from pypy.rlib.objectmodel import we_are_translated
from pypy.rlib.streamio import open_file_as_stream
from pypy.lang.js.jscode import JsCode
from pypy.rlib.rarithmetic import NAN, INFINITY, isnan, isinf

ASTBUILDER = ASTBuilder()

def writer(x):
    print x

def load_source(script_source, sourcename):
    temp_tree = parse(script_source)
    ASTBUILDER.sourcename = sourcename
    return ASTBUILDER.dispatch(temp_tree)

def load_file(filename):
    f = open_file_as_stream(filename)
    t = load_source(f.readall(), filename)
    f.close()
    return t

class W_NativeObject(W_Object):
    def __init__(self, Class, Prototype, ctx=None,
                 Value=w_Undefined, callfunc=None):
        W_Object.__init__(self, ctx, Prototype,
                          Class, Value, callfunc)
    
class W_ObjectObject(W_NativeObject):
    def Call(self, ctx, args=[], this=None):
        if len(args) >= 1 and not isnull_or_undefined(args[0]):
            return args[0].ToObject(ctx)
        else:
            return self.Construct(ctx)

    def Construct(self, ctx, args=[]):
        if (len(args) >= 1 and not args[0] is w_Undefined and not
            args[0] is w_Null):
            # XXX later we could separate builtins and normal objects
            return args[0].ToObject(ctx)
        return create_object(ctx, 'Object')

class W_BooleanObject(W_NativeObject):
    def Call(self, ctx, args=[], this=None):
        if len(args) >= 1 and not isnull_or_undefined(args[0]):
            return W_Boolean(args[0].ToBoolean())
        else:
            return W_Boolean(False)

    def Construct(self, ctx, args=[]):
        if len(args) >= 1 and not isnull_or_undefined(args[0]):
            Value = W_Boolean(args[0].ToBoolean())
            return create_object(ctx, 'Boolean', Value = Value)
        return create_object(ctx, 'Boolean', Value = W_Boolean(False))

class W_NumberObject(W_NativeObject):
    def Call(self, ctx, args=[], this=None):
        if len(args) >= 1 and not isnull_or_undefined(args[0]):
            return W_FloatNumber(args[0].ToNumber(ctx))
        elif len(args) >= 1 and args[0] is w_Undefined:
            return W_FloatNumber(NAN)
        else:
            return W_FloatNumber(0.0)

    def ToNumber(self, ctx):
        return 0.0

    def Construct(self, ctx, args=[]):
        if len(args) >= 1 and not isnull_or_undefined(args[0]):
            Value = W_FloatNumber(args[0].ToNumber(ctx))
            return create_object(ctx, 'Number', Value = Value)
        return create_object(ctx, 'Number', Value = W_FloatNumber(0.0))

class W_StringObject(W_NativeObject):
    def Call(self, ctx, args=[], this=None):
        if len(args) >= 1:
            return W_String(args[0].ToString(ctx))
        else:
            return W_String('')

    def Construct(self, ctx, args=[]):
        if len(args) >= 1:
            Value = W_String(args[0].ToString(ctx))
            return create_object(ctx, 'String', Value = Value)
        return create_object(ctx, 'String', Value = W_String(''))

class W_ArrayObject(W_NativeObject):
    def Call(self, ctx, args=[], this=None):
        proto = ctx.get_global().Get(ctx, 'Array').Get(ctx, 'prototype')
        array = W_Array(ctx, Prototype=proto, Class = proto.Class)
        for i in range(len(args)):
            array.Put(ctx, str(i), args[i])
        return array

    def Construct(self, ctx, args=[]):
        return self.Call(ctx, args)

TEST = False

def evaljs(ctx, args, this):
    if len(args) >= 1:
        if  isinstance(args[0], W_String):
            src = args[0].strval
        else:
            return args[0]
    else:
        src = ''
    try:
        node = load_source(src, 'evalcode')
    except ParseError, e:
        raise ThrowException(W_String('SyntaxError: '+str(e)))

    bytecode = JsCode()
    node.emit(bytecode)
    return bytecode.run(ctx, retlast=True)

def parseIntjs(ctx, args, this):
    if len(args) < 1:
        return W_FloatNumber(NAN)
    s = args[0].ToString(ctx).strip(" ")
    if len(args) > 1:
        radix = args[1].ToInt32(ctx)
    else:
        radix = 10
    if len(s) >= 2 and (s.startswith('0x') or s.startswith('0X')) :
        radix = 16
        s = s[2:]
    if s == '' or radix < 2 or radix > 36:
        return W_FloatNumber(NAN)
    try:
        n = int(s, radix)
    except ValueError:
        return W_FloatNumber(NAN)
    return W_IntNumber(n)

def parseFloatjs(ctx, args, this):
    if len(args) < 1:
        return W_FloatNumber(NAN)
    s = args[0].ToString(ctx).strip(" ")
    try:
        n = float(s)
    except ValueError:
        n = NAN
    return W_FloatNumber(n)
    

def printjs(ctx, args, this):
    writer(",".join([i.ToString(ctx) for i in args]))
    return w_Undefined

def isnanjs(ctx, args, this):
    if len(args) < 1:
        return W_Boolean(True)
    return W_Boolean(isnan(args[0].ToNumber(ctx)))

def isfinitejs(ctx, args, this):
    if len(args) < 1:
        return W_Boolean(True)
    n = args[0].ToNumber(ctx)
    if  isinf(n) or isnan(n):
        return W_Boolean(False)
    else:
        return W_Boolean(True)
        
def absjs(ctx, args, this):
    val = args[0]
    if isinstance(val, W_IntNumber):
        if val.intval > 0:
            return val # fast path
        return W_IntNumber(-val.intval)
    return W_FloatNumber(abs(args[0].ToNumber(ctx)))

def floorjs(ctx, args, this):
    return W_IntNumber(int(math.floor(args[0].ToNumber(ctx))))

def powjs(ctx, args, this):
    return W_FloatNumber(math.pow(args[0].ToNumber(ctx), args[1].ToNumber(ctx)))

def sqrtjs(ctx, args, this):
    return W_FloatNumber(math.sqrt(args[0].ToNumber(ctx)))

def versionjs(ctx, args, this):
    return w_Undefined

class W_ToString(W_NewBuiltin):
    def Call(self, ctx, args=[], this=None):
        return W_String("[object %s]"%this.Class)

class W_ValueOf(W_NewBuiltin):
    def Call(self, ctx, args=[], this=None):
        return this

class W_HasOwnProperty(W_NewBuiltin):
    def Call(self, ctx, args=[], this=None):
        if len(args) >= 1:
            propname = args[0].ToString(ctx)
            if propname in this.propdict:
                return W_Boolean(True)
        return W_Boolean(False)

class W_IsPrototypeOf(W_NewBuiltin):
    def Call(self, ctx, args=[], this=None):
        if len(args) >= 1 and isinstance(args[0], W_PrimitiveObject):
            O = this
            V = args[0].Prototype
            while V is not None:
                if O == V:
                    return W_Boolean(True)
                V = V.Prototype
        return W_Boolean(False)

class W_PropertyIsEnumerable(W_NewBuiltin):
    def Call(self, ctx, args=[], this=None):
        if len(args) >= 1:
            propname = args[0].ToString(ctx)
            if propname in this.propdict and not this.propdict[propname].de:
                return W_Boolean(True)
        return W_Boolean(False)

class W_Function(W_NewBuiltin):
    def Call(self, ctx, args=[], this=None):
        tam = len(args)
        if tam >= 1:
            fbody  = args[tam-1].ToString(ctx)
            argslist = []
            for i in range(tam-1):
                argslist.append(args[i].ToString(ctx))
            fargs = ','.join(argslist)
            functioncode = "function (%s) {%s}"%(fargs, fbody)
        else:
            functioncode = "function () {}"
        #remove program and sourcelements node
        funcnode = parse(functioncode).children[0].children[0]
        ast = ASTBUILDER.dispatch(funcnode)
        bytecode = JsCode()
        ast.emit(bytecode)
        return bytecode.run(ctx, retlast=True)
    
    def Construct(self, ctx, args=[]):
        return self.Call(ctx, args, this=None)

functionstring= 'function (arguments go here!) {\n'+ \
                '    [lots of stuff :)]\n'+ \
                '}'
class W_FToString(W_NewBuiltin):
    def Call(self, ctx, args=[], this=None):
        if this.Class == 'Function':
            return W_String(functionstring)
        else:
            raise JsTypeError('this is not a function object')

class W_Apply(W_NewBuiltin):
    def Call(self, ctx, args=[], this=None):
        try:
            if isnull_or_undefined(args[0]):
                thisArg = ctx.get_global()
            else:
                thisArg = args[0].ToObject(ctx)
        except IndexError:
            thisArg = ctx.get_global()
        
        try:
            arrayArgs = args[1]
            if isinstance(arrayArgs, W_ListObject):
                callargs = arrayArgs.tolist()
            elif isnull_or_undefined(arrayArgs):
                callargs = []
            else:
                raise JsTypeError('arrayArgs is not an Array or Arguments object')
        except IndexError:
            callargs = []
        return this.Call(ctx, callargs, this=thisArg)

class W_Call(W_NewBuiltin):
    def Call(self, ctx, args=[], this=None):
        if len(args) >= 1:
            if isnull_or_undefined(args[0]):
                thisArg = ctx.get_global()
            else:
                thisArg = args[0]
            callargs = args[1:]
        else:
            thisArg = ctx.get_global()
            callargs = []
        return this.Call(ctx, callargs, this = thisArg)

class W_ValueToString(W_NewBuiltin):
    "this is the toString function for objects with Value"
    mytype = ''
    def Call(self, ctx, args=[], this=None):
        if this.Value.type() != self.mytype:
            raise JsTypeError('Wrong type')
        return W_String(this.Value.ToString(ctx))


class W_NumberValueToString(W_ValueToString):
    mytype = 'number'

class W_BooleanValueToString(W_ValueToString):
    mytype = 'boolean'

class W_StringValueToString(W_ValueToString):
    mytype = 'string'


def get_value_of(type, ctx):
    class W_ValueValueOf(W_NewBuiltin):
        "this is the valueOf function for objects with Value"
        def Call(self, ctx, args=[], this=None):
            if type != this.Class:
                raise JsTypeError('%s.prototype.valueOf called with incompatible type' % self.type())
            return this.Value
    return W_ValueValueOf(ctx)
        
class W_CharAt(W_NewBuiltin):
    def Call(self, ctx, args=[], this=None):
        string = this.ToString(ctx)
        if len(args)>=1:
            pos = args[0].ToInt32(ctx)
            if (not pos >=0) or (pos > len(string) - 1):
                return W_String('')
        else:
            return W_String('')
        return W_String(string[pos])

class W_Concat(W_NewBuiltin):
    def Call(self, ctx, args=[], this=None):
        string = this.ToString(ctx)
        others = [obj.ToString(ctx) for obj in args]
        string += ''.join(others)
        return W_String(string)

class W_IndexOf(W_NewBuiltin):
    def Call(self, ctx, args=[], this=None):
        string = this.ToString(ctx)
        if len(args) < 1:
            return W_IntNumber(-1)
        substr = args[0].ToString(ctx)
        size = len(string)
        subsize = len(substr)
        if len(args) < 2:
            pos = 0
        else:
            pos = args[1].ToInt32(ctx)
        pos = min(max(pos, 0), size)
        return W_IntNumber(string.find(substr, pos))

class W_Substring(W_NewBuiltin):
    def Call(self, ctx, args=[], this=None):
        string = this.ToString(ctx)
        size = len(string)
        if len(args) < 1:
            start = 0
        else:
            start = args[0].ToInt32(ctx)
        if len(args) < 2:
            end = size
        else:
            end = args[1].ToInt32(ctx)
        tmp1 = min(max(start, 0), size)
        tmp2 = min(max(end, 0), size)
        start = min(tmp1, tmp2)
        end = max(tmp1, tmp2)
        return W_String(string[start:end])

class W_ArrayToString(W_NewBuiltin):
    def Call(self, ctx, args=[], this=None):
        length = this.Get(ctx, 'length').ToUInt32(ctx)
        sep = ','
        return W_String(sep.join([this.Get(ctx, str(index)).ToString(ctx) 
                            for index in range(length)]))

class W_DateFake(W_NewBuiltin): # XXX This is temporary
    def Call(self, ctx, args=[], this=None):
        return create_object(ctx, 'Object')
    
    def Construct(self, ctx, args=[]):
        return create_object(ctx, 'Object')

def pypy_repr(ctx, repr, w_arg):
    return W_String(w_arg.__class__.__name__)

class Interpreter(object):
    """Creates a js interpreter"""
    def __init__(self):
        def put_values(obj, dictvalues):
            for key,value in dictvalues.iteritems():
                obj.Put(ctx, key, value)
            
        w_Global = W_Object(Class="global")

        ctx = global_context(w_Global)
        
        w_ObjPrototype = W_Object(Prototype=None, Class='Object')
        
        w_Function = W_Function(ctx, Class='Function', 
                              Prototype=w_ObjPrototype)
        
        w_Global.Put(ctx, 'Function', w_Function)
        
        w_Object = W_ObjectObject('Object', w_Function)
        w_Object.Put(ctx, 'prototype', w_ObjPrototype, dd=True, de=True, ro=True)
        
        w_Global.Put(ctx, 'Object', w_Object)
        w_FncPrototype = w_Function.Call(ctx, this=w_Function)
        w_Function.Put(ctx, 'prototype', w_FncPrototype, dd=True, de=True, ro=True)
        w_Function.Put(ctx, 'constructor', w_Function)
        
        w_Object.Put(ctx, 'length', W_IntNumber(1), ro=True, dd=True)
        
        toString = W_ToString(ctx)
        
        put_values(w_ObjPrototype, {
            'constructor': w_Object,
            '__proto__': w_Null,
            'toString': toString,
            'toLocaleString': toString,
            'valueOf': W_ValueOf(ctx),
            'hasOwnProperty': W_HasOwnProperty(ctx),
            'isPrototypeOf': W_IsPrototypeOf(ctx),
            'propertyIsEnumerable': W_PropertyIsEnumerable(ctx),
        })
        
        #properties of the function prototype
        put_values(w_FncPrototype, {
            'constructor': w_FncPrototype,
            '__proto__': w_ObjPrototype,
            'toString': W_FToString(ctx),
            'apply': W_Apply(ctx),
            'call': W_Call(ctx),        
        })
        
        w_Boolean = W_BooleanObject('Boolean', w_FncPrototype)
        w_Boolean.Put(ctx, 'constructor', w_FncPrototype, dd=True, ro=True, de=True)
        w_Boolean.Put(ctx, 'length', W_IntNumber(1), dd=True, ro=True, de=True)
        
        w_BoolPrototype = create_object(ctx, 'Object', Value=W_Boolean(False))
        w_BoolPrototype.Class = 'Boolean'
        
        put_values(w_BoolPrototype, {
            'constructor': w_FncPrototype,
            '__proto__': w_ObjPrototype,
            'toString': W_BooleanValueToString(ctx),
            'valueOf': get_value_of('Boolean', ctx),
        })

        w_Boolean.Put(ctx, 'prototype', w_BoolPrototype, dd=True, ro=True, de=True)

        w_Global.Put(ctx, 'Boolean', w_Boolean)

        #Number
        w_Number = W_NumberObject('Number', w_FncPrototype)

        w_empty_fun = w_Function.Call(ctx, args=[W_String('')])

        w_NumPrototype = create_object(ctx, 'Object', Value=W_FloatNumber(0.0))
        w_NumPrototype.Class = 'Number'
        put_values(w_NumPrototype, {
            'constructor': w_Number,
            '__proto__': w_empty_fun,
            'toString': W_NumberValueToString(ctx),
            'valueOf': get_value_of('Number', ctx),
        })

        put_values(w_Number, {
            'constructor': w_FncPrototype,
            'prototype': w_NumPrototype,
            '__proto__': w_empty_fun,
            'length'   : W_IntNumber(1),
        })
        w_Number.propdict['prototype'].ro = True
        w_Number.Put(ctx, 'MAX_VALUE', W_FloatNumber(1.7976931348623157e308),
                     ro=True, dd=True)
        w_Number.Put(ctx, 'MIN_VALUE', W_FloatNumber(0), ro=True, dd=True)
        w_Number.Put(ctx, 'NaN', W_FloatNumber(NAN), ro=True, dd=True)
        # ^^^ this is exactly in test case suite
        w_Number.Put(ctx, 'POSITIVE_INFINITY', W_FloatNumber(INFINITY),
                     ro=True, dd=True)
        w_Number.Put(ctx, 'NEGATIVE_INFINITY', W_FloatNumber(-INFINITY),
                     ro=True, dd=True)
        

        w_Global.Put(ctx, 'Number', w_Number)
        
                
        #String
        w_String = W_StringObject('String', w_FncPrototype)

        w_StrPrototype = create_object(ctx, 'Object', Value=W_String(''))
        w_StrPrototype.Class = 'String'
        
        put_values(w_StrPrototype, {
            'constructor': w_FncPrototype,
            '__proto__': w_StrPrototype,
            'toString': W_StringValueToString(ctx),
            'valueOf': get_value_of('String', ctx),
            'charAt': W_CharAt(ctx),
            'concat': W_Concat(ctx),
            'indexOf': W_IndexOf(ctx),
            'substring': W_Substring(ctx),
        })
        
        w_String.Put(ctx, 'prototype', w_StrPrototype)
        w_Global.Put(ctx, 'String', w_String)

        w_Array = W_ArrayObject('Array', w_FncPrototype)

        w_ArrPrototype = create_object(ctx, 'Object')
        w_ArrPrototype.Class = 'Array'
        
        put_values(w_ArrPrototype, {
            'constructor': w_FncPrototype,
            '__proto__': w_ArrPrototype,
            'toString': W_ArrayToString(ctx),
        })
        
        w_Array.Put(ctx, 'prototype', w_ArrPrototype)
        w_Global.Put(ctx, 'Array', w_Array)
        
        
        #Math
        w_math = W_Object(Class='Math')
        w_Global.Put(ctx, 'Math', w_math)
        w_math.Put(ctx, '__proto__',  w_ObjPrototype)
        w_math.Put(ctx, 'prototype', w_ObjPrototype, dd=True, de=True, ro=True)
        w_math.Put(ctx, 'abs', W_Builtin(absjs, Class='function'))
        w_math.Put(ctx, 'floor', W_Builtin(floorjs, Class='function'))
        w_math.Put(ctx, 'pow', W_Builtin(powjs, Class='function'))
        w_math.Put(ctx, 'sqrt', W_Builtin(sqrtjs, Class='function'))
        w_math.Put(ctx, 'E', W_FloatNumber(math.e))
        w_math.Put(ctx, 'PI', W_FloatNumber(math.pi))
        
        w_Global.Put(ctx, 'version', W_Builtin(versionjs))
        
        #Date
        w_Date = W_DateFake(ctx, Class='Date')
        w_Global.Put(ctx, 'Date', w_Date)
        
        w_Global.Put(ctx, 'NaN', W_FloatNumber(NAN))
        w_Global.Put(ctx, 'Infinity', W_FloatNumber(INFINITY))
        w_Global.Put(ctx, 'undefined', w_Undefined)
        w_Global.Put(ctx, 'eval', W_Builtin(evaljs))
        w_Global.Put(ctx, 'parseInt', W_Builtin(parseIntjs))
        w_Global.Put(ctx, 'parseFloat', W_Builtin(parseFloatjs))
        w_Global.Put(ctx, 'isNaN', W_Builtin(isnanjs))
        w_Global.Put(ctx, 'isFinite', W_Builtin(isfinitejs))            

        w_Global.Put(ctx, 'print', W_Builtin(printjs))
        w_Global.Put(ctx, 'this', w_Global)

        # DEBUGGING
        if 0:
            w_Global.Put(ctx, 'pypy_repr', W_Builtin(pypy_repr))
        
        self.global_context = ctx
        self.w_Global = w_Global
        self.w_Object = w_Object

    def run(self, script, interactive=False):
        """run the interpreter"""
        bytecode = JsCode()
        script.emit(bytecode)
        if not we_are_translated():
            # debugging
            self._code = bytecode
        if interactive:
            return bytecode.run(self.global_context, retlast=True)
        else:
            bytecode.run(self.global_context)

def wrap_arguments(pyargs):
    "receives a list of arguments and wrap then in their js equivalents"
    res = []
    for arg in pyargs:
        if isinstance(arg, W_Root):
            res.append(arg)
        elif isinstance(arg, str):
            res.append(W_String(arg))
        elif isinstance(arg, int):
            res.append(W_IntNumber(arg))
        elif isinstance(arg, float):
            res.append(W_FloatNumber(arg))
        elif isinstance(arg, bool):
            res.append(W_Boolean(arg))
        else:
            raise Exception("Cannot wrap %s" % (arg,))
    return res
