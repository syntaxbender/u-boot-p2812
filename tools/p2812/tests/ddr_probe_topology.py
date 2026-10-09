"""DDR probe-address aliases, with independent controller/chip geometry.

Only the historical probe words are modeled here. This is not a complete
DRAM bus/timing model or a capacity measurement. Four-bank chips can alias
BA2 at 0x2000 with an eight-bank controller setting while retaining the
top row bit at 0x08000000; a flat size-only alias model misses that case.
"""
import struct

class ProbeTopology:
    addresses=(0,0x200,0x400,0x2000,0x04000000,0x08000000)

    def __init__(self,uc,banks,rows,columns=10):
        if banks not in (4,8) or rows not in (13,14) or columns not in (9,10):
            raise ValueError('Unsupported model geometry')
        self.uc=uc
        self.bank_bits=2 if banks==4 else 3
        self.rows=rows
        self.columns=columns
        self.cells={}

    def word(self,address):
        return struct.unpack('>I',self.uc.mem_read(address,4))[0]

    def decode(self,address):
        c3=self.word(0x1f401030);c11=self.word(0x1f4010b0)
        ctrl_cols=12-((c11>>24)&7)
        ctrl_banks=3 if c3&1 else 2
        ctrl_rows=14-(c11&7)
        column=(address>>1)&((1<<ctrl_cols)-1)&((1<<self.columns)-1)
        bank=(address>>(1+ctrl_cols))&((1<<ctrl_banks)-1)&((1<<self.bank_bits)-1)
        row=(address>>(1+ctrl_cols+ctrl_banks))&((1<<ctrl_rows)-1)&((1<<self.rows)-1)
        return (((row<<self.bank_bits)|bank)<<self.columns|column)<<1|(address&1)

    def write(self,uc,access,address,size,value,user):
        address&=0x1fffffff
        if address not in self.addresses or size!=4:
            raise AssertionError('Unexpected geometry probe write')
        self.cells[self.decode(address)]=value

    def read_zero(self,uc,access,address,size,value,user):
        if (address&0x1fffffff)!=0 or size!=4:
            raise AssertionError('Unexpected geometry read')
        uc.mem_write(0,struct.pack('>I',self.cells.get(self.decode(0),0)))

    def install(self):
        from unicorn import UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE
        for address in self.addresses:
            self.uc.hook_add(UC_HOOK_MEM_WRITE,self.write,begin=address,end=address+3)
        self.uc.hook_add(UC_HOOK_MEM_READ,self.read_zero,begin=0,end=3)
