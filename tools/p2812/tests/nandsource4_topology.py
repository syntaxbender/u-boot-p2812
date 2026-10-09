"""Probe-word model for NANDSOURCE4, with independently set chip geometry.

Only six historical addresses are modeled. Timing faults below are explicit
injections, not a theory established on hardware. No native macOS Unicorn.
"""
import struct
from ddr_probe_topology import ProbeTopology
class TraceTopology(ProbeTopology):
 def __init__(self,uc,banks,rows,mode):
  super().__init__(uc,banks,rows);self.mode=mode
 def read(self,uc,access,address,size,value,user):
  address&=0x1fffffff
  if address not in self.addresses or size!=4:raise AssertionError('Unexpected trace probe read')
  stage=self.word(0x1e22fa08)
  actual=self.cells.get(self.decode(address),0)
  if address==0 and stage in (1,2,3):
   if self.mode=='zero_probe':actual=0
   elif self.mode=='wrong_eight' and stage in (1,2):actual=1
   elif self.mode=='invalid_bank' and stage==1:actual=2
   elif self.mode=='invalid_row' and stage==2:actual=3
   elif self.mode=='invalid_col' and stage==3:actual=3
  if stage==5:
   if self.mode=='alias_zero':actual=0
   elif self.mode=='boundary_fault' and address==0x08000000:actual^=1
  uc.mem_write(address,struct.pack('>I',actual))
 def install(self):
  from unicorn import UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE
  for address in self.addresses:
   self.uc.hook_add(UC_HOOK_MEM_WRITE,self.write,begin=address,end=address+3)
   self.uc.hook_add(UC_HOOK_MEM_READ,self.read,begin=address,end=address+3)
