from rpython.jit.backend.zarch import conditions as c
from rpython.jit.backend.zarch import registers as r
from rpython.jit.backend.zarch import locations as l
from rpython.jit.backend.zarch.arch import STD_FRAME_SIZE_IN_BYTES
from rpython.jit.backend.zarch.instruction_builder import build_instr_codes
from rpython.jit.backend.llsupport.asmmemmgr import BlockBuilderMixin
from rpython.jit.backend.llsupport.assembler import GuardToken
from rpython.rlib.objectmodel import we_are_translated
from rpython.rlib.unroll import unrolling_iterable
from rpython.rtyper.lltypesystem import lltype, rffi, llmemory
from rpython.tool.udir import udir
from rpython.jit.backend.detect_cpu import autodetect
from rpython.jit.backend.zarch.arch import WORD

clear_cache = rffi.llexternal(
    "__clear_cache",
    [llmemory.Address, llmemory.Address],
    lltype.Void,
    _nowrapper=True,
    sandboxsafe=True)

def binary_helper_call(name):
    function = getattr(support, 'arm_%s' % name)

    def f(self, c=c.AL):
        """Generates a call to a helper function, takes its
        arguments in r0 and r1, result is placed in r0"""
        addr = rffi.cast(lltype.Signed, function)
        self.BL(addr, c)
    return f

class ZARCHGuardToken(GuardToken):
    def __init__(self, cpu, gcmap, descr, failargs, faillocs,
                 guard_opnum, frame_depth, fcond=c.cond_none):
        GuardToken.__init__(self, cpu, gcmap, descr, failargs, faillocs,
                            guard_opnum, frame_depth)
        self.fcond = fcond
        self._pool_offset = -1

class AbstractZARCHBuilder(object):

    def write_i64(self, word):
        self.writechar(chr((word >> 56) & 0xFF))
        self.writechar(chr((word >> 48) & 0xFF))
        self.writechar(chr((word >> 40) & 0xFF))
        self.writechar(chr((word >> 32) & 0xFF))
        self.writechar(chr((word >> 24) & 0xFF))
        self.writechar(chr((word >> 16) & 0xFF))
        self.writechar(chr((word >> 8) & 0xFF))
        self.writechar(chr(word & 0xFF))

    def write_i32(self, word):
        self.writechar(chr((word >> 24) & 0xFF))
        self.writechar(chr((word >> 16) & 0xFF))
        self.writechar(chr((word >> 8) & 0xFF))
        self.writechar(chr(word & 0xFF))

    def write_i16(self, word):
        self.writechar(chr((word >> 8) & 0xFF))
        self.writechar(chr(word & 0xFF))

    def write(self, bytestr):
        for char in bytestr:
            self.writechar(char)

build_instr_codes(AbstractZARCHBuilder)

class InstrBuilder(BlockBuilderMixin, AbstractZARCHBuilder):

    RAW_CALL_REG = r.r14

    def __init__(self):
        AbstractZARCHBuilder.__init__(self)
        self.init_block_builder()
        #
        # ResOperation --> offset in the assembly.
        # ops_offset[None] represents the beginning of the code after the last op
        # (i.e., the tail of the loop)
        self.ops_offset = {}

    def mark_op(self, op):
        pos = self.get_relative_pos()
        self.ops_offset[op] = pos

    def _dump_trace(self, addr, name, formatter=-1):
        if not we_are_translated():
            if formatter != -1:
                name = name % formatter
            dir = udir.ensure('asm', dir=True)
            f = dir.join(name).open('wb')
            data = rffi.cast(rffi.CCHARP, addr)
            for i in range(self.currpos()):
                f.write(data[i])
            f.close() 
    def clear_cache(self, addr):
        if we_are_translated():
            startaddr = rffi.cast(llmemory.Address, addr)
            endaddr = rffi.cast(llmemory.Address,
                            addr + self.get_relative_pos())
            clear_cache(startaddr, endaddr)

    def copy_to_raw_memory(self, addr):
        self._copy_to_raw_memory(addr)
        self.clear_cache(addr)
        self._dump(addr, "jit-backend-dump", "s390x")

    def load(self, treg, sreg, offset):
        self.LG(treg, l.addr(offset, sreg))

    def store_update(self, valreg, treg, offset):
        self.STG(valreg, l.addr(offset, treg))

    def nop(self):
        # if the mask is zero it act as a NOP
        # there is no special 'no operation' instruction
        self.BCR_rr(0x0, 0x0)

    def currpos(self):
        return self.get_relative_pos()

    def b_cond_offset(self, offset, condition):
        assert condition != c.cond_none
        self.BRC(condition, l.imm(offset))

    def b_offset(self, reladdr):
        offset = reladdr - self.get_relative_pos()
        self.BRC(c.ANY, l.imm(offset))

    def reserve_guard_branch(self):
        self.BRC(l.imm(0x0), l.imm(0))

    def trap(self):
        self.TRAP2()

    def trace(self):
        self.SVC(l.imm(142))
        #self.LGHI(r.r2, 17)
        #self.XGR(r.r3, r.r3)
        #self.SVC(l.imm(17))

    def cmp_op(self, a, b, pool=False, imm=False, signed=True, fp=False):
        if fp == True:
            if pool:
                self.CDB(a, b)
            else:
                self.CDBR(a, b)
        else:
            if signed:
                if pool:
                    # 64 bit immediate signed
                    self.CG(a, b)
                elif imm:
                    self.CGFI(a, b)
                else:
                    # 64 bit signed
                    self.CGR(a, b)
            else:
                if pool:
                    # 64 bit immediate unsigned
                    self.CLG(a, b)
                elif imm:
                    self.CLGFI(a, b)
                else:
                    # 64 bit unsigned
                    self.CLGR(a, b)


    def load_imm(self, dest_reg, word):
        if -32768 <= word <= 32767:
            self.LGHI(dest_reg, l.imm(word))
        elif -2**31 <= word <= 2**31-1:
            self.LGFI(dest_reg, l.imm(word))
        else:
            # this is not put into the constant pool, because it
            # is an immediate value that cannot easily be forseen
            self.LGFI(dest_reg, l.imm(word & 0xFFFFffff))
            self.IIHF(dest_reg, l.imm((word >> 32) & 0xFFFFffff))

    def load_imm_plus(self, dest_reg, word):
        """Like load_imm(), but with one instruction less, and
        leaves the loaded value off by some signed 16-bit difference.
        Returns that difference."""
        diff = rffi.cast(lltype.Signed, rffi.cast(rffi.SHORT, word))
        word -= diff
        assert word & 0xFFFF == 0
        self.load_imm(dest_reg, word)
        return diff

    def sync(self):
        # see sync. section of the zarch manual!
        self.BCR_rr(0xf,0)

    def raw_call(self, call_reg=r.RETURN):
        """Emit a call to the address stored in the register 'call_reg',
        which must be either RAW_CALL_REG or r12.  This is a regular C
        function pointer, which means on big-endian that it is actually
        the address of a three-words descriptor.
        """
        self.BASR(r.RETURN, call_reg)

    def reserve_cond_jump(self):
        self.trap()        # conditional jump, patched later
        self.write('\x00'*4)

    def branch_absolute(self, addr):
        self.load_imm(r.r14, addr)
        self.BASR(r.r14, r.r14)

    def store_link(self):
        self.STG(r.RETURN, l.addr(14*WORD, r.SP))

    def restore_link(self):
        self.LG(r.RETURN, l.addr(14*WORD, r.SP))

    def push_std_frame(self):
        self.STG(r.SP, l.addr(-STD_FRAME_SIZE_IN_BYTES, r.SP))
        self.LAY(r.SP, l.addr(-STD_FRAME_SIZE_IN_BYTES, r.SP))

    def pop_std_frame(self):
        self.LAY(r.SP, l.addr(STD_FRAME_SIZE_IN_BYTES, r.SP))

    def get_assembler_function(self):
        "NOT_RPYTHON: tests only"
        from rpython.jit.backend.llsupport.asmmemmgr import AsmMemoryManager
        class FakeCPU:
            HAS_CODEMAP = False
            asmmemmgr = AsmMemoryManager()
        addr = self.materialize(FakeCPU(), [])
        return rffi.cast(lltype.Ptr(lltype.FuncType([], lltype.Signed)), addr)

class OverwritingBuilder(BlockBuilderMixin, AbstractZARCHBuilder):
    def __init__(self, mc, start, num_insts=0):
        AbstractZARCHBuilder.__init__(self)
        self.init_block_builder()
        self.mc = mc
        self.index = start

    def writechar(self, c):
        self.mc.overwrite(self.index, c)
        self.index += 1

    def overwrite(self):
        pass

_classes = (AbstractZARCHBuilder,)

# Used to build the MachineCodeBlockWrapper
all_instructions = sorted([name for cls in _classes for name in cls.__dict__ \
                          if name.split('_')[0].isupper() and '_' in name and \
                             not name.endswith('_byte_count')])
