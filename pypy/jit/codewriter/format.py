import py
from pypy.objspace.flow.model import Constant
from pypy.rpython.lltypesystem import lltype
from pypy.jit.codewriter.flatten import SSARepr, Label, TLabel, Register
from pypy.jit.codewriter.flatten import ListOfKind, IndirectCallTargets
from pypy.jit.codewriter.jitcode import SwitchDictDescr
from pypy.jit.metainterp.history import AbstractDescr


def format_assembler(ssarepr):
    """For testing: format a SSARepr as a multiline string."""
    from cStringIO import StringIO
    seen = {}
    #
    def repr(x):
        if isinstance(x, Register):
            return '%%%s%d' % (x.kind[0], x.index)    # e.g. %i1 or %r2 or %f3
        elif isinstance(x, Constant):
            if (isinstance(x.concretetype, lltype.Ptr) and
                isinstance(x.concretetype.TO, lltype.Struct)):
                return '$<* struct %s>' % (x.concretetype.TO._name,)
            return '$%r' % (x.value,)
        elif isinstance(x, TLabel):
            return getlabelname(x)
        elif isinstance(x, ListOfKind):
            return '%s[%s]' % (x.kind[0].upper(), ', '.join(map(repr, x)))
        elif isinstance(x, SwitchDictDescr):
            return '<SwitchDictDescr %s>' % (
                ', '.join(['%s:%s' % (key, getlabelname(lbl))
                           for key, lbl in x._labels]))
        elif isinstance(x, (AbstractDescr, IndirectCallTargets)):
            return '%r' % (x,)
        else:
            return '<unknown object: %r>' % (x,)
    #
    seenlabels = {}
    for asm in ssarepr.insns:
        for x in asm:
            if isinstance(x, TLabel):
                seenlabels[x.name] = -1
            elif isinstance(x, SwitchDictDescr):
                for _, switch in x._labels:
                    seenlabels[switch.name] = -1
    labelcount = [0]
    def getlabelname(lbl):
        if seenlabels[lbl.name] == -1:
            labelcount[0] += 1
            seenlabels[lbl.name] = labelcount[0]
        return 'L%d' % seenlabels[lbl.name]
    #
    output = StringIO()
    for asm in ssarepr.insns:
        if isinstance(asm[0], Label):
            if asm[0].name in seenlabels:
                print >> output, '%s:' % getlabelname(asm[0])
        else:
            print >> output, asm[0],
            if len(asm) > 1:
                lst = map(repr, asm[1:])
                if asm[0] == '-live-': lst.sort()
                print >> output, ', '.join(lst)
            else:
                print >> output
    res = output.getvalue()
    return res

def assert_format(ssarepr, expected):
    asm = format_assembler(ssarepr)
    expected = str(py.code.Source(expected)).strip() + '\n'
    asmlines = asm.split("\n")
    explines = expected.split("\n")
    for asm, exp in zip(asmlines, explines):
        if asm != exp:
            print
            print "Got:      " + asm
            print "Expected: " + exp
            lgt = 0
            for i in range(min(len(asm), len(exp))):
                if exp[i] == asm[i]:
                    lgt += 1
                else:
                    break
            print "          " + " " * lgt + "^^^^"
            raise AssertionError
    assert len(asmlines) == len(explines)

def unformat_assembler(text, registers=None):
    # XXX limited to simple assembler right now
    #
    def unformat_arg(s):
        if s[0] == '%':
            try:
                return registers[s]
            except KeyError:
                num = int(s[2:])
                if s[1] == 'i': reg = Register('int', num)
                elif s[1] == 'r': reg = Register('ref', num)
                elif s[1] == 'f': reg = Register('float', num)
                else: raise AssertionError("bad register type")
                registers[s] = reg
                return reg
        elif s[0] == '$':
            intvalue = int(s[1:])
            return Constant(intvalue, lltype.Signed)
        elif s[0] == 'L':
            return TLabel(s)
        else:
            raise AssertionError("unsupported argument: %r" % (s,))
    #
    if registers is None:
        registers = {}
    ssarepr = SSARepr('test')
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('L') and line.endswith(':'):
            ssarepr.insns.append((Label(line[:-1]),))
        else:
            try:
                opname, line = line.split(None, 1)
            except ValueError:
                opname, line = line, ''
            line = [s.strip() for s in line.split(',')]
            insn = [opname] + [unformat_arg(s) for s in line if s]
            ssarepr.insns.append(tuple(insn))
    return ssarepr
