from pypy.jit.codegen import model
from pypy.rpython.lltypesystem import lltype, rffi, llmemory
# Wrapper Classes:
# The opcodes(assemble.py) differ from the type of
# the operand(eg. Register, Immediate...). 
# The to_string method is used to choose the right
# method inside the assembler

class Register64(object):
    def __init__(self, reg):
        self.reg = reg
        
class Register8(object):
    def __init__(self, reg):
        self.reg = reg
        
class Stack64(object):
    def __init__(self, offset):
        self.offset = offset

class IntVar(model.GenVar):
    def __init__(self, location):
        self.location = location
        assert isinstance(location, Register64) or isinstance(location, Register8) or isinstance(location, Stack64)
    
    def to_string(self):
        if isinstance(self.location, Stack64): 
            return "_STACK"
        if isinstance(self.location, Register8): 
            return "_8REG"
        elif isinstance(self.location, Register64): 
            return "_QWREG"

class Immediate8(model.GenConst):
    def __init__(self, value):
        self.value = value
        
    def to_string(self):
        return "_IMM8"
    
class Immediate32(model.GenConst):
    def __init__(self, value):
        self.value = value
        
    def to_string(self):
        return "_IMM32"
    
# TODO: understand GenConst
class Immediate64(model.GenConst):
    def __init__(self, value):
        self.value = value
        
    def to_string(self):
        return "_IMM64"