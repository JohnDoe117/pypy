from pypy.jit.codegen import model
from pypy.jit.codegen.x86_64.objmodel import IntVar, Register8, Register64, Immediate8, Immediate32, Immediate64, Stack64

# Mapping from 64Bit-Register to coding (Rex.W or Rex.B , ModRM)
REGISTER_MAP = {
                "rax": (0, 0),
                "rcx": (0, 1),
                "rdx": (0, 2),
                "rbx": (0, 3),
                "rsp": (0, 4),
                "rbp": (0, 5),
                "rsi": (0, 6),
                "rdi": (0, 7),
                "r8":  (1, 0),
                "r9":  (1, 1),
                "r10": (1, 2),
                "r11": (1, 3),
                "r12": (1, 4),
                "r13": (1, 5),
                "r14": (1, 6),
                "r15": (1, 7),
                }
                
# Mapping from 8Bit-Register to coding
REGISTER_MAP_8BIT = {
                    "al":0,
                    "cl":1,
                    "dl":2,
                    }
                    
# This method wirtes the bitencodings into
# the memory. The parameters are overwritten
# if one of the operands is an register.
# tttn isn't used yet
# extra is an extra byte for long opcodes(starting with 0F) like IMUL
def make_two_operand_instr(W = None, R = None, X = None, B = None, opcode =None, m = None, md1 = None, md2 = None, tttn = None, extra = None):
    def quadreg_instr(self, arg1, arg2):
        # move the parameter 
        # to the inner function
        mod = m
        modrm1 = md1
        modrm2 = md2
        rexW = W
        rexR = R
        rexX = X
        rexB = B
        # TODO: other cases e.g memory as operand
        if isinstance(arg1,IntVar):
            if isinstance(arg1.location, Register64):
                rexB, modrm1 = self.get_register_bits(arg1.location.reg)
            elif isinstance(arg1.location, Register8):
                modrm1 = self.get_register_bits_8Bit(arg1.location.reg)
            # special case: Stack and Register exchanged
            elif isinstance(arg1.location, Stack64) and isinstance(arg2.location, Register64):
                rexB, modrm1 = self.get_register_bits(arg2.location.reg)
                self.write_rex_byte(rexW, rexB, rexX, rexR) 
                self.write(opcode)
                # exchanged mod1,mod2, dont know why :)
                self.write_modRM_byte(mod, modrm1, modrm2)
                # no scale(has no effect on rsp), no index, base = rsp
                self.write_SIB(0, 4, 4) 
                self.writeImm32(8*arg1.location.offset) #8 == scale
                return quadreg_instr 
            
        # exchange the two arguments (modrm2/modrm1)
        if isinstance(arg2,Immediate32):
            # e.g: IMUL (source==dest)
            if(modrm2=="sameReg"):
                modrm2 = modrm1
                rexR = rexB
            self.write_rex_byte(rexW, rexR, rexX, rexB)
            self.write(opcode)
            self.write_modRM_byte(3, modrm2, modrm1)
            self.writeImm32(arg2.value)
        elif isinstance(arg2,Immediate8):
            self.write_rex_byte(rexW, rexR, rexX, rexB)
            self.write(opcode)
            self.write_modRM_byte(3, modrm2, modrm1)
            self.write(chr(arg2.value)) 
        elif isinstance(arg2, IntVar):
            if isinstance(arg2.location, Register64):
                rexR, modrm2 = self.get_register_bits(arg2.location.reg)         
                # FIXME: exchange the two arguments (rexB/rexR)
                self.write_rex_byte(rexW, rexR, rexX, rexB)
                self.write(opcode)
                # used in imul (extra long opcode)
                if not extra == None:
                    self.write(extra)
                self.write_modRM_byte(mod, modrm2, modrm1)
            elif isinstance(arg2.location, Stack64):
                self.write_rex_byte(rexW, rexB, rexX, rexR) 
                self.write(opcode)
                # exchanged mod1,mod2, dont know why :)
                self.write_modRM_byte(mod, modrm1, modrm2)
                # no scale(has no effect on rsp), no index, base = rsp
                self.write_SIB(0, 4, 4) 
                self.writeImm32(8*arg2.location.offset) #8==scale
    return quadreg_instr
        
        
# This method wirtes the bitencodings into
# the memory. The parameters are overwritten
# if one of the operands is an register.
# tttn codes the flags and is only used by SETcc
def make_one_operand_instr(W = None, R = None, X = None, B = None, opcode = None, m = None, md1 = None, md2 = None, tttn=None, extra = None):
    def quadreg_instr(self, arg1):       
        # move the parameter 
        # to the inner function
        mod = m
        modrm1 = md1
        modrm2 = md2
        rexW = W
        rexR = R
        rexX = X
        rexB = B
        
        # TODO: other cases e.g memory as operand
        if isinstance(arg1, IntVar):
            if isinstance(arg1.location, Register64):
                rexB, modrm1 = self.get_register_bits(arg1.location.reg)
            elif isinstance(arg1.location, Register8):
                modrm1 = self.get_register_bits_8Bit(arg1.location.reg)
            
        # rexW(1) = 64bitMode 
        self.write_rex_byte(rexW, rexR, rexX, rexB)
        self.write(opcode)
        if not tttn == None: #write 0F9X
            byte = (9 << 4) | tttn
            self.write(chr(byte))
        self.write_modRM_byte(mod, modrm2, modrm1)        
    return quadreg_instr
        
# When using an alternate encoding the opcode
# depends on the used register X0 to XF.     
def make_two_operand_instr_with_alternate_encoding(W = None, R = None, X = None, B = None, opcode =None, md1 = None, md2 = None):
    def quadreg_instr(self, arg1, arg2):
        # move the parameter 
        # to the inner function
        modrm1 = md1
        modrm2 = md2
        rexW = W
        rexR = R #not used?
        rexX = X #not used?
        rexB = B
        # TODO: other cases e.g memory as operand
        # FIXME: rexB?
        if isinstance(arg1, IntVar):
            if isinstance(arg1.location, Register64):
                rexB, modrm1 = self.get_register_bits(arg1.location.reg)
              
        if isinstance(arg2, Immediate64):
            new_opcode = hex(int(opcode,16)+modrm1)
            assert len(new_opcode[2:len(new_opcode)]) == 2
            self.write_rex_byte(rexW, rexR, rexX, rexB)
            self.write(chr(int(new_opcode[2:len(new_opcode)],16)))
            self.writeImm64(arg2.value)
    return quadreg_instr
        
def make_one_operand_instr_with_alternate_encoding(W = None, R = None, X = None, B = None, opcode =None, md1 = None, md2 = None):
    def quadreg_instr(self, arg1):
        # move the parameter 
        # to the inner function
        modrm1 = md1
        modrm2 = md2
        rexW = W
        rexR = R
        rexX = X
        rexB = B
        # TODO: other cases e.g memory as operand
        # FIXME: rexB?
        if isinstance(arg1, IntVar):
            if isinstance(arg1.location, Register64):
                rexB, modrm1 = self.get_register_bits(arg1.location.reg)
                new_opcode = hex(int(opcode,16)+modrm1)
                assert len(new_opcode[2:len(new_opcode)]) == 2
                self.write_rex_byte(rexW, rexR, rexX, rexB)
                self.write(chr(int(new_opcode[2:len(new_opcode)],16)))
    return quadreg_instr

class X86_64CodeBuilder(object):
    """ creats x86_64 opcodes"""
    def write(self, data):
        """ writes data into memory"""
        raise NotImplementedError
    
    def tell(self):
        """ tells the current position in memory"""
        raise NotImplementedError
    
    #TODO: support all combinations
    # The opcodes differs depending on the operands
    # Params:
    # W (Width, 64bit Operands), R (Register, extends reg field), X (IndeX, extend Index(SIB) field), B (Base, extends r/m field, Base(SIB) field, opcode reg field), 
    # Opcode, mod, modrm1, modrm2, tttn(JUMPS), extraopcode 
    
    # FIXME: rexB is set
    _ADD_QWREG_IMM32 = make_two_operand_instr(   1,    0,    0,    0, "\x81", 3, None, 0)  
    _ADD_QWREG_QWREG = make_two_operand_instr(   1, None,    0, None, "\x01", 3, None, None)
    
    _AND_QWREG_QWREG = make_two_operand_instr(   1, None,    0, None, "\x21", 3, None, None)
    
    # FIXME: rexB is set
    # maybe a bug
    _CMP_QWREG_IMM32 = make_two_operand_instr(   1,    0,    0,    1, "\x81", 3, None, 7)
    _CMP_QWREG_QWREG = make_two_operand_instr(   1, None,    0, None, "\x39", 3, None, None)
    # FIXME: rexB is set
    _CMP_8REG_IMM8   = make_two_operand_instr(   0,    0,    0, None, "\x80", 3, None, 7)
    
    _DEC_QWREG       = make_one_operand_instr(   1,    0,    0, None, "\xFF", 3, None, 1)
    _INC_QWREG       = make_one_operand_instr(   1,    0,    0, None, "\xFF", 3, None, 0)

     
    _MOV_QWREG_IMM32 = make_two_operand_instr(   1,    0,    0, None, "\xC7", 3, None, 0)
    _MOV_QWREG_QWREG = make_two_operand_instr(   1, None,    0, None, "\x89", 3, None, None)
    _MOV_QWREG_IMM64 = make_two_operand_instr_with_alternate_encoding(1,0,0,None,"B8",None,None)
    _MOV_QWREG_STACK = make_two_operand_instr(   1,    0,    0, None, "\x8B", 2, None, 4)#4==RSP
    _MOV_STACK_QWREG = make_two_operand_instr(   1,    0,    0, None, "\x89", 2, None, 4)    
        
    _IDIV_QWREG      = make_one_operand_instr(   1,    0,    0, None, "\xF7", 3, None, 7)
    
    _IMUL_QWREG_QWREG = make_two_operand_instr(  1, None,    0, None, "\x0F", 3, None, None, None, "\xAF")
    _IMUL_QWREG_IMM32 = make_two_operand_instr(  1, None,    0, None, "\x69", 3, None, "sameReg")#3 op instr
    
    _NEG_QWREG       = make_one_operand_instr(   1,    0,    0, None, "\xF7", 3, None, 3)
    
    _NOT_QWREG       = make_one_operand_instr(   1,    0,    0, None, "\xF7", 3, None, 2)
    
    _JMP_QWREG       = make_one_operand_instr(   1,    0,    0, None, "\xFF", 3, None, 4)
    
    _OR_QWREG_QWREG  = make_two_operand_instr(   1, None,    0, None, "\x09", 3, None, None)

    # FIXME: rexW is set 
    _POP_QWREG       = make_one_operand_instr(   1,    0,    0, None, "\x8F", 3, None, 0)
    _PUSH_QWREG      = make_one_operand_instr(   1,    0,    0, None, "\xFF", 3, None, 6)
     
    #_POP_QWREG  = make_one_operand_instr_with_alternate_encoding(1,0,0,None,"58",None,None)
    #_PUSH_QWREG = make_one_operand_instr_with_alternate_encoding(1,0,0,None,"50",None,None) 
     
    _SETE_8REG       = make_one_operand_instr(   0,    0,    0,    0, "\x0F", 3, None, 0,4) 
    _SETG_8REG       = make_one_operand_instr(   0,    0,    0,    0, "\x0F", 3, None, 0,15)
    _SETGE_8REG      = make_one_operand_instr(   0,    0,    0,    0, "\x0F", 3, None, 0,13)
    _SETL_8REG       = make_one_operand_instr(   0,    0,    0,    0, "\x0F", 3, None, 0,12) 
    _SETLE_8REG      = make_one_operand_instr(   0,    0,    0,    0, "\x0F", 3, None, 0,14)   
    _SETNE_8REG      = make_one_operand_instr(   0,    0,    0,    0, "\x0F", 3, None, 0,5)  
     
    _SHL_QWREG       = make_one_operand_instr(   1,    0,    0, None, "\xD3", 3, None, 4)
    _SHR_QWREG       = make_one_operand_instr(   1,    0,    0, None, "\xD3", 3, None, 5) 
     
    _SUB_QWREG_QWREG = make_two_operand_instr(   1, None,    0, None, "\x29", 3, None, None)    
    _SUB_QWREG_IMM32 = make_two_operand_instr(   1,    0,    0,    0, "\x81", 3, None, 5)
    
    _XOR_QWREG_IMM32 = make_two_operand_instr(   1,    0,    0, None, "\x81", 3, None, 6)
    _XOR_QWREG_QWREG = make_two_operand_instr(   1, None,    0, None, "\x31", 3, None, None)
    
    # TODO(all ops): maybe a problem with more ore less than two arg.
    def ADD(self, op1, op2):
        method = getattr(self, "_ADD"+op1.to_string()+op2.to_string())
        method(op1, op2)
        
    def AND(self, op1, op2):
        method = getattr(self, "_AND"+op1.to_string()+op2.to_string())
        method(op1, op2)
     
    def CDQ(self):
        self.write("\x48")
        self.write("\x99")
    
    def CMP(self, op1, op2):
        #import pdb;pdb.set_trace()
        method = getattr(self, "_CMP"+op1.to_string()+op2.to_string())
        method(op1, op2)
        
    def DEC(self, op1):
        method = getattr(self, "_DEC"+op1.to_string())
        method(op1)
                
    def IDIV(self, op1):
        method = getattr(self, "_IDIV"+op1.to_string())
        method(op1)
            
    def IMUL(self, op1, op2):
        method = getattr(self, "_IMUL"+op1.to_string()+op2.to_string())
        # exchange the two arguments because 
        # the result is in the first register 
        if(op1.to_string()=="_QWREG" and op2.to_string()=="_QWREG"):
            # after this operation, op2 contains 
            # the genVar/result, dont throw is away
            op1.location.contains_genConst = False
            op1.location.dont_spill(False)
            method(op2, op1)
        else:
            method(op1, op2)
        
    def INC(self, op1):
        method = getattr(self, "_INC"+op1.to_string())
        method(op1)
        
    # 4 length of the immediate
    # want_jump_to is an 32bit(!) adress
    def JMP(self,want_jump_to):
        #method = getattr(self, "_JMP"+op1.to_string())
        #method(want_jump_to)  
        self.write("\xE9")
        self.writeImm32(want_jump_to-self.tell()-4)
        
    # 4 length of the immediate
    def JNE(self,want_jump_to):
        self.write("\x0F")
        self.write("\x85")
        self.writeImm32(want_jump_to-self.tell()-4)   
        
        
    def OR(self, op1, op2):
        method = getattr(self, "_OR"+op1.to_string()+op2.to_string())
        method(op1, op2)
        
    #FIXME: "POP/PUSH NONE" BUG- (maybe fixed)
    def POP(self, op1):
        method = getattr(self, "_POP"+op1.to_string())
        method(op1)
        
    def PUSH(self, op1):
        method = getattr(self, "_PUSH"+op1.to_string())
        method(op1)
        
    # TODO: support Register8
    def MOV(self, op1, op2):
        method = getattr(self, "_MOV"+op1.to_string()+op2.to_string())
        method(op1, op2)
            
    def NEG(self, op1):
        method = getattr(self, "_NEG"+op1.to_string())
        method(op1)
        
    def NOT(self, op1):
        method = getattr(self, "_NOT"+op1.to_string())
        method(op1)
    
    def RET(self):
        self.write("\xC3")
        
    def SETG(self, op1):
        method = getattr(self, "_SETG"+op1.to_string())
        method(op1)
        
    def SETL(self, op1):
        method = getattr(self, "_SETL"+op1.to_string())
        method(op1)
        
    def SETR(self, op1):
        method = getattr(self, "_SETR"+op1.to_string())
        method(op1)
        
    def SETGE(self, op1):
        method = getattr(self, "_SETGE"+op1.to_string())
        method(op1)
        
    def SETLE(self, op1):
        method = getattr(self, "_SETLE"+op1.to_string())
        method(op1)
        
    def SETE(self, op1):
        method = getattr(self, "_SETE"+op1.to_string())
        method(op1)
        
    def SETNE(self, op1):
        method = getattr(self, "_SETNE"+op1.to_string())
        method(op1)
        
    def SHL(self, op1):
        method = getattr(self, "_SHL"+op1.to_string())
        method(op1)
        
    def SHR(self, op1):
        method = getattr(self, "_SHR"+op1.to_string())
        method(op1)
        
    def SUB(self, op1, op2):
        method = getattr(self, "_SUB"+op1.to_string()+op2.to_string())
        method(op1, op2)
        
    def XOR(self, op1, op2):
        method = getattr(self, "_XOR"+op1.to_string()+op2.to_string())
        method(op1, op2)
        
    def get_register_bits(self, register):
        return REGISTER_MAP[register]
    
    def get_register_bits_8Bit(self, register):
        return REGISTER_MAP_8BIT[register]
    

    # Parse the integervalue to an charakter
    # and write it
    def writeImm32(self, imm32):
        x = hex(imm32)
        if x[0]=='-':
            # parse to string and cut "0x" off
            # fill with Fs if to short
            x = self.cast_neg_hex32(int(x,16))
            y = "F"*(8-len(x))+x[0:len(x)]
        else:            
            # parse to string and cut "0x" off
            # fill with zeros if to short
            y = "0"*(10-len(x))+x[2:len(x)]
        assert len(y) == 8           
        self.write(chr(int(y[6:8],16))) 
        self.write(chr(int(y[4:6],16)))
        self.write(chr(int(y[2:4],16)))
        self.write(chr(int(y[0:2],16)))
        
        
    # TODO: sign extension?
    # Parse the integervalue to an character
    # and write it
    def writeImm64(self, imm64):
        x = hex(imm64)
        # parse to string and cut "0x" off
        # fill with zeros if to short
        y = "0"*(18-len(x))+x[2:len(x)]
        assert len(y) == 16    
        self.write(chr(int(y[14:16],16))) 
        self.write(chr(int(y[12:14],16)))
        self.write(chr(int(y[10:12],16)))
        self.write(chr(int(y[8:10],16)))        
        self.write(chr(int(y[6:8],16))) 
        self.write(chr(int(y[4:6],16)))
        self.write(chr(int(y[2:4],16)))
        self.write(chr(int(y[0:2],16)))
            
    
    # Rex-Prefix 4WRXB see AMD vol3 page 45
    def write_rex_byte(self, rexW, rexR, rexX, rexB):
        byte = (4 << 4) | (rexW << 3) | (rexR << 2) | (rexX << 1) | rexB
        self.write(chr(byte))
        
    def write_modRM_byte(self, mod, reg, rm):
        byte = mod << 6 | (reg << 3) | rm
        self.write(chr(byte))
        
    def write_SIB(self, scale, index, base):
        byte = scale << 6 | (index << 3) | base
        self.write(chr(byte))
        
    # calc. the "Two's complement"
    # and return as pos. hex value.
    # This method is used to calc jump displ.
    def cast_neg_hex32(self,a_int):
        x = hex(int("FFFFFFFF",16)+1 +a_int)
        y = x[2:len(x)]
        return y

