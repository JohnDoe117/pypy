from pypy.rlib.objectmodel import instantiate
from pypy.lang.io.model import W_Number, W_Object, W_CFunction
from pypy.lang.io.register import cfunction_definitions

import pypy.lang.io.number
import pypy.lang.io.object

class ObjSpace(object):
    """docstring for ObjSpace"""
    def __init__(self):
        self.init_w_object()
        self.w_lobby = W_Object(self)
        self.w_protos = W_Object(self)
        self.w_core = W_Object(self)
        
        self.w_core.protos.append(self.w_object)
        
        self.w_protos.protos.append(self.w_core)
        self.w_protos.slots['Core'] = self.w_core
        
        self.init_w_lobby()
        
        self.init_w_number()
        
    def init_w_number(self):
        self.w_number = instantiate(W_Number)
        W_Object.__init__(self.w_number, self)
        self.w_number.value = 0
        for key, function in cfunction_definitions['Number'].items():
            self.w_number.slots[key] = W_CFunction(self, function)
            
    def init_w_lobby(self):
        self.w_lobby.protos.append(self.w_protos)
        self.w_lobby.slots['Lobby'] = self.w_lobby
        self.w_lobby.slots['Protos'] = self.w_protos

    def init_w_object(self):
        self.w_object = W_Object(self)
        for key, function in cfunction_definitions['Object'].items():
            self.w_object.slots[key] = W_CFunction(self, function)