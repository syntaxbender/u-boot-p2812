#!/usr/bin/env python3
"""Execute compiled board hook with bounded GPIO and GPHY clock register models.

No actual MMIO/electrical PHY behavior or complete board-init sequence emulated.
Only console puts is stubbed. Actual compiled GPIO/clock code executes unchanged.
"""
import argparse,hashlib,json,platform,random,struct,sys,tempfile,zipfile
from pathlib import Path
from model_support import prepare,physical,TEXT,RELOC,GD,SENTINEL
WHEEL_SHA='9d6e6dea140560de4ebd8446661f7ef84a357d428c14a3ef09dacd306ec8c239'
REGS=[0x1e100b78,0x1e100b84,0x1e100b7c,0x1e100b80]
ORDER=[REGS[2],REGS[3],REGS[1],REGS[0]]
IFCLK=0x1f103024
CLOCK_MASK=0x1c
def main():
 p=argparse.ArgumentParser();p.add_argument('main',type=Path);a=p.parse_args();assert platform.system()=='Linux'
 wheel=Path('/inputs/unicorn.whl');assert hashlib.sha256(wheel.read_bytes()).hexdigest()==WHEEL_SHA
 assert 'CONFIG_BOARD_EARLY_INIT_R=y' in (a.main/'.config').read_text()
 syms,f,raw,relocs=prepare(a.main);addr=lambda n:syms[n]+RELOC-TEXT
 cases=[];rng=random.Random(2812)
 inputs=[('cold',[0x800,0x400,0,0],0x10e00000),('zero',[0]*4,0),
         ('already',[0xc00]*3+[0],0x10e00008),
         ('random',[rng.getrandbits(32) for i in range(4)],rng.getrandbits(32))]
 with tempfile.TemporaryDirectory(prefix='p2812-mdio-') as tmp:
  with zipfile.ZipFile(wheel) as z:
   assert all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in z.namelist());z.extractall(tmp)
  sys.path.insert(0,tmp)
  from unicorn import Uc,UC_ARCH_MIPS,UC_MODE_MIPS32,UC_MODE_BIG_ENDIAN,UC_HOOK_CODE,UC_HOOK_MEM_WRITE,UC_HOOK_MEM_READ
  import unicorn.mips_const as m
  for name,before,clock_before in inputs:
   u=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_BIG_ENDIAN);u.ctl_set_cpu_model(m.UC_CPU_MIPS32_34KF)
   u.mem_map(physical(RELOC),0x100000);u.mem_map(0x3000000,0x500000);u.mem_map(0x1e100000,0x10000)
   u.mem_map(0x1f103000,0x1000)
   u.mem_write(physical(RELOC),raw);u.reg_write(m.UC_MIPS_REG_K0,GD)
   for reg,v in zip(REGS,before):u.mem_write(reg,struct.pack('>I',v))
   u.mem_write(IFCLK,struct.pack('>I',clock_before))
   writes=[];clock_writes=[]
   def store(uc,access,address,size,value,_):
    address=physical(address)
    if address>=0x1e000000:
     assert address in REGS+[IFCLK] and size==4,('UnexpectedMMIOwrite',hex(address),size)
     writes.append(address)
     if address==IFCLK:clock_writes.append(value)
   def load(uc,access,address,size,value,_):
    address=physical(address)
    if address>=0x1e000000:
     assert address in REGS+[IFCLK] and size==4,('UnexpectedMMIOread',hex(address),size)
   def code(uc,pc,size,_):
    if pc==addr('puts'):
     uc.reg_write(m.UC_MIPS_REG_V0,0);uc.reg_write(m.UC_MIPS_REG_PC,uc.reg_read(m.UC_MIPS_REG_RA))
   u.hook_add(UC_HOOK_MEM_WRITE,store);u.hook_add(UC_HOOK_MEM_READ,load);u.hook_add(UC_HOOK_CODE,code)
   expected=[before[0]|0xc00,before[1]|0xc00,before[2]|0xc00,before[3]&~0xc00]
   expected_clock=(clock_before&~CLOCK_MASK)|0x8
   for repeat in range(2):
    u.reg_write(m.UC_MIPS_REG_SP,0xa33fe000);u.reg_write(m.UC_MIPS_REG_RA,SENTINEL)
    u.emu_start(addr('board_early_init_r'),SENTINEL,count=10000)
    assert u.reg_read(m.UC_MIPS_REG_PC)==SENTINEL and u.reg_read(m.UC_MIPS_REG_SP)==0xa33fe000
    assert u.reg_read(m.UC_MIPS_REG_V0)==0
    after=[struct.unpack('>I',u.mem_read(reg,4))[0] for reg in REGS]
    assert after==expected and writes[repeat*5:]==ORDER+[IFCLK],(name,repeat,after,writes)
    assert all(((x^y)&~0xc00)==0 for x,y in zip(before,after))
    clock_after=struct.unpack('>I',u.mem_read(IFCLK,4))[0]
    assert clock_after==expected_clock and (clock_after^clock_before)&~CLOCK_MASK==0
    assert clock_writes==[expected_clock]*(repeat+1)
   cases.append({'name':name,'passed':True,'calls':2,'GPIO_writes_per_call':4,'GPHY_clock_writes_per_call':1,'clock_before':f'{clock_before:08x}','clock_after':f'{expected_clock:08x}','GPIO_and_clock_outside_masks_unchanged':True,'idempotent':True,'caller_SP_preserved':True})
 result={'passed':True,'hardware_access':False,'real_function':'board_early_init_r','raw_sha256':hashlib.sha256((a.main/'u-boot.bin').read_bytes()).hexdigest(),'relocations':relocs,'cases':cases,'limits':'ActualcompiledGPIO/clockcode/registermodels; putsstub; nofullboot/electricalPHY/DDR/NANDproof.'}
 (a.main/'mdio-handoff-tests.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
