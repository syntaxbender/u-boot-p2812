#!/usr/bin/env python3
"""Actual source-built SPL->DDR sequence->NAND/ECC->LZO->entry instructions.

Linux-only Unicorn; ROM copy, EBU/GPIO/DDR/NAND timing, topology and caches
are models. NAND fixture has synthetic OOB; no hardware or Linux execution.
"""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import platform
import re
import struct
import sys
import tempfile
import zipfile
import zlib
import subprocess
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from nandsource4_topology import TraceTopology
ROOT=Path(__file__).resolve().parents[1]/'tests'
WHEEL=Path('/inputs/unicorn.whl')
WHEEL_SHA='9d6e6dea140560de4ebd8446661f7ef84a357d428c14a3ef09dacd306ec8c239'
CONTEXT=ROOT/'fixtures/rom-uart-entry.json'
CONTEXT_SHA='76a931a2ab6dd9aeed3b3d690438a31a3149e53f9025199bac9a9ab42c4f69c4'
COLD=ROOT/'fixtures/rom-ddr-clock.json'
COLD_SHA='ced4c351e79d43268eebde3481a22828c6bef1648403ae22bc447d8428c48e6a'
def phys(a):return a&0x1fffffff if 0x80000000<=a<0xc0000000 else a
def symbols(path):
 return {p[2]:int(p[0],16)&0xffffffff for line in path.read_text().splitlines() if len(p:=line.split())==3}
def header_crc(data):
 data[0x8004:0x8008]=bytes(4);struct.pack_into('>I',data,0x8004,zlib.crc32(data[0x8000:0x8040])&0xffffffff)
def payload_crc(data):
 struct.pack_into('>I',data,0x8018,zlib.crc32(data[0x8040:])&0xffffffff);header_crc(data)
def make_oob(data,lib):
 data=data.ljust(0x40000,b'\xff');pages=[];buf=(ctypes.c_ubyte*256)();ecc=(ctypes.c_ubyte*3)()
 for off in range(0,len(data),2048):
  page=data[off:off+2048];oob=bytearray(b'\xff'*64)
  for step in range(8):
   buf[:]=page[step*256:step*256+256];lib.nand_calculate_ecc(None,buf,ecc)
   oob[40+step*3:43+step*3]=bytes(ecc)
  pages.append(bytearray(page+oob))
 return pages
def emulate(image,expected,early,pages,fill=0,mode='ok',profile='inherited_nand_model',banks=8,rows=13):
 from unicorn import Uc,UC_ARCH_MIPS,UC_MODE_MIPS32,UC_MODE_BIG_ENDIAN,UC_HOOK_CODE,UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE
 import unicorn.mips_const as m
 mu=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_BIG_ENDIAN);mu.ctl_set_cpu_model(m.UC_CPU_MIPS32_34KF)
 regions=[(0,0x10000),(0x00100000,0x100000),(0x01000000,0x40000),(0x00400000,0x10000),
          (0x04000000,0x1000),(0x08000000,0x1000),(0x1e220000,0x10000),
          (0x1e105000,0x1000),(0x1e100000,0x1000),(0x14000000,0x1000),
          (0x1f102000,0x2000),(0x1f400000,0x2000)]
 for address,size in regions:mu.mem_map(address,size)
 mu.mem_write(0x1e220000,bytes([fill])*0x10000)
 mu.reg_write(m.UC_MIPS_REG_CP0_STATUS,0x400001)
 mu.reg_write(m.UC_MIPS_REG_SP,0xa7000000)
 # Model only the ROM download command's initial copy; execution starts
 # at the first newly built bootstrap instruction, not after DDR init.
 size=struct.unpack_from('>I',image,4)[0]-4
 mu.mem_write(0x1e220000,image[12:12+size])
 def word(a):return struct.unpack('>I',mu.mem_read(a,4))[0]
 def setword(a,v):mu.mem_write(a,struct.pack('>I',v))
 before=[int(v,16) for v in json.loads(CONTEXT.read_text())['observation']['entry_words']]
 for off,v in zip((0,0x10,0x20,0x24,0x60,0x64,0xb0,0xb4),before[3:11]):setword(0x1e105300+off,v)
 for off,v in zip((0x24,0x20,0x18,0x1c,0x54,0x50,0x48,0x4c,0xa8,0xac,0x64,0x34),before[11:23]):
  setword(0x1e100b00+off,v)
 setword(0x1f10201c,before[1]);setword(0x1f102020,before[2])
 measured=json.loads(COLD.read_text())
 cold=[int(v,16) for v in measured['clock_words']]
 ready=[int(v,16) for v in measured['ready_words']]
 for off,v in zip((0xc,0x10,4,8,0x60),cold[4:9]):setword(0x1f103000+off,v)
 for n,v in zip((3,8,11,15,16),ready[6:11]):setword(0x1f401000+n*16,v)
 if mode=='already_clock_target':setword(0x1f10300c,0x12);setword(0x1f103010,0x3010000)
 if mode=='unknown_clock':setword(0x1f10300c,0x33)
 if mode=='pll_unlocked':setword(0x1f103008,cold[7]&~2)
 if mode=='uart_garbage':
  for off in (0,0x10,0x50,0x58):setword(0x1e100c00+off,0xffffffff)
 if profile=='inherited_nand_model':
  # Hypothetical NAND-ROM state, deliberately distinct from measured UART:
  # retain non-default region1 timing until TPL performs explicit setup.
  setword(0x1e105324,0x14000031);setword(0x1e105364,0x0021a6b5)
  setword(0x1e1053b0,0x5f3)
 elif profile!='rom_uart_register_fixture':raise ValueError(profile)
 if mode=='already_disabled':setword(0x1e105320,word(0x1e105320)&~1)
 if mode=='nondefault_addrsel0':setword(0x1e105320,0x1a009035)
 splsy=symbols(early/'spl-symbols.txt');tplsy=symbols(early/'tpl-symbols.txt')
 result={'initial_sram_fill':fill,'entry_profile':profile,'handoff':False,'nand_commands':[],
         'pages_read':[],'ddr_starts':0,'clock_update':False,'error':None,
         'error_stage':None,'ebu_guards':[],'spl_inherited_nand_config_preserved':False,
         'clock_writes':[],'cache_i':0,'cache_d':0}
 state={'cmd':None,'addresses':[],'page':0,'column':0,'reads':0,'id_index':0,
        'pmu_write':False,'guard':None,'addrsel0_reads':0,'region0_written':False,
        'guard_failed':False,'tpl_started':False,'pre_nand_failed':False,
        'dll47_reads':0,'injected':False,'memory_calls':0}
 guard_other=[0x1f10201c,0x1f102020,0x1e105300,0x1e105310,
              0x1e105324,0x1e105360,0x1e105364,0x1e1053b0,0x1e1053b4]
 guard_other += [0x1e100b00+off for off in (0x24,0x20,0x18,0x1c,0x54,0x50,0x48,0x4c,0xa8,0xac,0x64,0x34)]
 spl_before={a:word(a) for a in guard_other if a not in (0x1e1053b0,0x1e1053b4)}
 def guard_enter(uc,address,size,user):
  stage='spl' if address==splsy['ebu_prepare'] else 'tpl'
  if stage=='tpl':state['tpl_started']=True
  fault=mode[len(stage)+1:] if mode.startswith(stage+'_') else ''
  if fault=='fpim_gated':setword(0x1f102020,word(0x1f102020)|0x4000)
  if fault=='ebu_gated':setword(0x1f102020,word(0x1f102020)|0x400)
  if fault=='clc_disabled':setword(0x1e105300,word(0x1e105300)|2)
  if stage=='tpl' and (mode=='tpl_region0_reenabled' or fault in ('remap_stuck','remap_other_changed')):
   setword(0x1e105320,word(0x1e105320)|1)
  record={'stage':stage,'fault':fault,'before_addrsel0':'%08x'%word(0x1e105320),
          'other_before':{'%08x'%a:'%08x'%word(a) for a in guard_other},'io':[]}
  result['ebu_guards'].append(record);state['guard']=record
  state['addrsel0_reads']=0;state['region0_written']=False
  return_address=uc.reg_read(m.UC_MIPS_REG_RA)
  def guard_return(uc,address,size,user):
   assert record is state['guard'],'Unexpected guard return'
   record['status']=uc.reg_read(m.UC_MIPS_REG_V0)
   record['after_addrsel0']='%08x'%word(0x1e105320)
   record['other_after']={'%08x'%a:'%08x'%word(a) for a in guard_other}
   assert record['other_before']==record['other_after'],'Guard changed another register'
   writes=[v for v in record['io'] if v[0]=='write']
   if record['status']==0:
    original=int(record['before_addrsel0'],16)
    assert int(record['after_addrsel0'],16)==original&~1
    assert len(writes)==original&1
   state['guard']=None
   state['guard_failed']=record['status']!=0
  uc.hook_add(UC_HOOK_CODE,guard_return,begin=return_address,end=return_address)
 def log_guard(kind,address,size,value):
  if state['guard'] is not None:
   state['guard']['io'].append([kind,'%08x'%address,size,'%08x'%value])
 def read(uc,access,address,size,value,user):
  address=phys(address)
  assert not 0x1e100c00<=address<0x1e100d00,'TPL must not assume or read UART state'
  if state['guard'] is not None:
   fault=state['guard']['fault']
   if address==0x1e105300:
    assert not word(0x1f102020)&0x4400,'EBU read while FPIM/EBU gated'
   if address==0x1e105320:
    assert not word(0x1f102020)&0x4400 and not word(0x1e105300)&2
    state['addrsel0_reads']+=1
    if fault=='prewrite_changed' and state['addrsel0_reads']==2:setword(address,word(address)^0x100)
    if state['region0_written'] and fault in ('remap_stuck','remap_other_changed'):
     initial=int(state['guard']['before_addrsel0'],16)
     setword(address,initial if fault=='remap_stuck' else (initial&~1)^0x100)
  if address==0x1e1053b4:
   v=0 if mode=='spl_timeout' else 9
   uc.mem_write(address,struct.pack('>I',v))
  elif address==0x1f102020 and state['pmu_write']:
   initial=before[2]&~0x400
   setword(address,initial|0x400 if mode=='pmu_timeout' else initial)
  elif address in (0x1f4012f0,0x1f401300):
   if address==0x1f4012f0:state['dll47_reads']+=1
   v=0x63 if address==0x1f401300 and result['ddr_starts']==2 else 0x65
   if mode=='ddr_timeout' or (mode=='dll_flicker' and address==0x1f4012f0 and state['dll47_reads']==2):v=0
   setword(address,v)
  elif address==0x1f4010f0:setword(address,(word(address)&~0x0f000000)|(0 if mode=='invalid_capacity' else 12<<24))
  elif address==0x1f401100:setword(address,(word(address)&~15)|14|0x04000000)
  elif address==0x1f10300c:
   if mode=='clock_readback' and result['clock_writes']:setword(address,0x90)
   if mode=='clock_after_decode' and state['tpl_started'] and word(0x1e22fd28)==len(expected):setword(address,0x33)
  elif address==0x1f103008 and mode=='pll_changed' and result['clock_writes']:
   setword(address,cold[7]^4)
  elif address==0x14000010:
   if state['cmd']==0x90:
    vals=[0xec,0xf1,0,0x95,0x40];v=vals[state['id_index']%5]
    if mode=='wrong_id':v=0
    state['id_index']+=1
   else:
    assert state['cmd']==0x30 and state['reads']<2112,state
    assert state['page']<128,'Read outside boot partition'
    v=pages[state['page']][state['reads']];state['reads']+=1
   uc.mem_write(address,bytes([v]))
  log_guard('read',address,size,int.from_bytes(uc.mem_read(address,size),'big'))
 def write(uc,access,address,size,value,user):
  address=phys(address)
  assert not state['guard_failed'],'MMIO write after failed EBU guard (including NAND release)'
  assert not state['pre_nand_failed'],'MMIO/NAND release after early clock/DDR/memory failure'
  assert not 0x1e100c00<=address<0x1e100d00,'TPL must not configure UART'
  log_guard('write',address,size,value)
  if state['guard'] is not None:
   assert address==0x1e105320 and size==4 and value==word(address)&~1
   state['region0_written']=True
  if address==0x14000018:
   assert size==1 and value in (0,0x30,0x90),'Forbidden NAND command: '+hex(value)
   assert state['guard'] is None and result['ebu_guards'][-1]['status']==0
   assert not word(0x1e105320)&1,'NAND command with region0 enabled'
   if len(result['pages_read'])<15:
    assert value in (0,0x30),'SPL must inherit NAND setup without READID'
    assert all(word(a)==v for a,v in spl_before.items()),'SPL replaced inherited timing/GPIO'
    result['spl_inherited_nand_config_preserved']=True
   elif value==0x90:
    assert word(0x1e22fd0c)==5 and word(0x1e22fd14)==65536,'NAND setup before RAM checks'
   result['nand_commands'].append(value);state['cmd']=value
   if value in (0,0x90):state['addresses']=[];state['id_index']=0
   if value==0x30:
    a=state['addresses'];assert len(a)==4 and a[0]==a[1]==0
    state['page']=a[2]|a[3]<<8;state['reads']=0;result['pages_read'].append(state['page'])
  elif address==0x14000014:state['addresses'].append(value)
  elif 0x14000000<=address<0x14001000:raise AssertionError('NAND data programming bus cycle')
  elif address==0x1f401070 and value&0x100:result['ddr_starts']+=1
  elif 0x1f103000<=address<0x1f104000:
   expected_clock=[(0x1f10300c,0x12),(0x1f103010,0x3010000),(0x1f103020,1)]
   assert len(result['clock_writes'])<3 and (address,value)==expected_clock[len(result['clock_writes'])],'PLL/reset/extra clock write'
   result['clock_writes'].append([address,value])
   if address==0x1f103020:result['clock_update']=True
  elif address==0x1f10201c:state['pmu_write']=True
 def stop(uc,address,size,user):
  if address==0xa0100000:
   result['handoff']=True
   result['decoded_identical']=bytes(uc.mem_read(0x00100000,len(expected)))==expected
   result['decoded_sha256']=hashlib.sha256(bytes(uc.mem_read(0x00100000,len(expected)))).hexdigest()
   result['corrected_steps']=struct.unpack('>I',uc.mem_read(phys(tplsy['early_corrected']),4))[0]
   result['spl_corrected_steps']=struct.unpack('>I',uc.mem_read(0x1e22fc00,4))[0]
   uc.emu_stop()
  else:
   if address==splsy['spl_fail']+12:
    result['error']=word(0x1e22fc04);result['error_stage']='spl'
   else:result['error']=word(0x1e22fd08)
   uc.emu_stop()
 # EBU/GPIO/clock/DDR MMIO execute actual linked instructions. Models
 # answer register reads; no initialization functions are skipped.
 for begin,end in [(0x1e100000,0x1e100fff),(0x1e105000,0x1e105fff),(0x14000000,0x14000fff),
                   (0x1f102000,0x1f103fff),(0x1f400000,0x1f401fff)]:
  mu.hook_add(UC_HOOK_MEM_READ,read,begin=begin,end=end)
  mu.hook_add(UC_HOOK_MEM_WRITE,write,begin=begin,end=end)
 topology_mode=mode if mode in ('invalid_bank','invalid_row','invalid_col','alias_zero','boundary_fault') else 'ok'
 TraceTopology(mu,banks,rows,topology_mode).install()
 def memory_enter(uc,address,size,user):
  state['memory_calls']+=1;assert result['ddr_starts']==2,'Memory check before DDR starts'
  if mode=='retention' and state['memory_calls']==2:
   assert word(0x1e22fd1c)==zlib.crc32(image[0x8000:])
   a=0x01000080;uc.mem_write(a,bytes([uc.mem_read(a,1)[0]^1]));state['injected']=True
 def memory_fault(uc,access,address,size,value,user):
  if mode in ('memory_before','memory_after') and state['memory_calls']==(1 if mode=='memory_before' else 2) and not state['injected']:
   setword(0x00400020,0x12345678);state['injected']=True
 mu.hook_add(UC_HOOK_CODE,memory_enter,begin=tplsy['memory_check'],end=tplsy['memory_check'])
 if mode in ('memory_before','memory_after'):mu.hook_add(UC_HOOK_MEM_READ,memory_fault,begin=0x00400020,end=0x00400023)
 def failed(uc,address,size,user):
  result['error_stage']='tpl_ebu' if address==tplsy['ebu_fail'] else 'tpl'
  if word(0x1e22fd0c)<5:state['pre_nand_failed']=True
 for address in (tplsy['fail'],tplsy['ebu_fail']):mu.hook_add(UC_HOOK_CODE,failed,begin=address,end=address)
 def copy_check(uc,address,size,user):
  # Guard actual linked copy arguments without per-byte delay-slot hooks.
  dst=uc.reg_read(m.UC_MIPS_REG_A0)
  if 0xa1000000<=dst<0xa1040000:assert state['tpl_started'] and state['memory_calls']>=1
 mu.hook_add(UC_HOOK_CODE,copy_check,begin=tplsy['memcpy'],end=tplsy['memcpy'])
 def cache_count(uc,address,size,user):
  op=(word(phys(address))>>16)&31;result['cache_i' if op==8 else 'cache_d']+=1
 tplbin=(early/'tpl.bin').read_bytes()
 for at in range(0,len(tplbin)-3,4):
  insn=struct.unpack_from('>I',tplbin,at)[0]
  if insn>>26==0x2f and (insn>>16)&31 in (8,9):
   address=0xbe220800+at;mu.hook_add(UC_HOOK_CODE,cache_count,begin=address,end=address)
 for address in (splsy['ebu_prepare'],tplsy['ebu_prepare']):
  mu.hook_add(UC_HOOK_CODE,guard_enter,begin=address,end=address)
 require_spl=bytes(mu.mem_read(phys(splsy['spl_fail'])+8,8))
 assert struct.unpack('>2I',require_spl)==(0xac440000,0x1000ffff),'SPL terminal-handler layout changed'
 for address in (splsy['spl_fail']+12,tplsy['tpl_idle'],0xa0100000):
  mu.hook_add(UC_HOOK_CODE,stop,begin=address,end=address)
 mu.emu_start(0xbe220000,0,timeout=120000000,count=180000000)
 assert result['handoff'] or result['error'] is not None,'Instruction/time budget exhausted'
 if result['handoff']:
  assert result['decoded_identical'] and result['ddr_starts']==2 and result['clock_update']==(mode!='already_clock_target')
  assert max(result['pages_read'])<128
  result['ddr_ccr11']=hex(struct.unpack('>I',mu.mem_read(0x1f4010b0,4))[0])
  assert result['cache_i']==result['cache_d']==1024
 assert not mu.reg_read(m.UC_MIPS_REG_CP0_STATUS)&1
 assert 0xbe22c000<mu.reg_read(m.UC_MIPS_REG_SP)<0xbe22f000,'Stack outside SRAM reserve'
 if state['tpl_started']:
  stats=list(struct.unpack('>32I',mu.mem_read(0x1e22fd00,128)))
  trace=list(struct.unpack('>64I',mu.mem_read(0x1e22fa00,256)))
  clock=list(struct.unpack('>32I',mu.mem_read(0x1e22f900,128)))
  assert stats[:2]==[0x4e535234,1] and stats[31]==0x444f4e45
  result.update(stats_words=['%08x'%v for v in stats],trace_words=['%08x'%v for v in trace],clock_words=['%08x'%v for v in clock])
  if result['handoff']:
   assert stats[2:6]==[0,9,(1<<(rows+10))*banks*2,131072]
   assert stats[6:11]==[len(image)-0x8000,zlib.crc32(image[0x8000:]),zlib.crc32(image[0x8000:]),zlib.crc32(expected),len(expected)]
   assert stats[30]==0x4a554d50 and trace[2:4]==[6,0] and trace[11:13]==[22,2]
   assert stats[11]==result['corrected_steps']
  else:assert stats[2]==result['error']==trace[3] and stats[30]==0 and result['cache_i']==result['cache_d']==0
 result['chip_geometry']={'banks':banks,'rows':rows,'columns':10}
 result['uart_accesses']=0
 return result
def main():
 p=argparse.ArgumentParser();p.add_argument('output',type=Path);a=p.parse_args()
 if platform.system()!='Linux':raise RuntimeError('Native macOS Unicorn prohibited')
 if hashlib.sha256(WHEEL.read_bytes()).hexdigest()!=WHEEL_SHA:raise RuntimeError('Wheel changed')
 if hashlib.sha256(CONTEXT.read_bytes()).hexdigest()!=CONTEXT_SHA:raise RuntimeError('ROM observation changed')
 if hashlib.sha256(COLD.read_bytes()).hexdigest()!=COLD_SHA:raise RuntimeError('Cold ROMDDR1 observation changed')
 out=a.output;original=(out/'package/nandfinal1-main-area.img').read_bytes();expected=(out/'package/u-boot.bin').read_bytes()
 lib=ctypes.CDLL(str(out/'native/ecc.so'));cases={}
 with tempfile.TemporaryDirectory(prefix='nandsource4-unicorn-') as directory:
  with zipfile.ZipFile(WHEEL) as z:
   assert all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in z.namelist());z.extractall(directory)
  sys.path.insert(0,directory)
  def run(label,image=None,mode='ok',fill=0,fault=None,error=None,profile='inherited_nand_model',banks=8,rows=13):
   assert label not in cases,'Duplicate case label'
   data=original if image is None else bytes(image);pages=make_oob(data,lib)
   if fault:fault(pages)
   r=emulate(data,expected,out/'early',pages,fill,mode,profile,banks,rows)
   if error is None:assert r['handoff'],(label,r)
   else:assert not r['handoff'] and r['error']==error,(label,r)
   if error is None:
    assert len(r['ebu_guards'])==2 and all(v['status']==0 for v in r['ebu_guards'])
    assert r['spl_inherited_nand_config_preserved']
   cases[label]=r;print('PASS: '+label,flush=True)
  run('cold_model_zero_sram');run('cold_model_ff_sram',fill=255)
  data=bytearray(original);data[3*2048+20]^=1
  run('spl_tpl_crc_reject_with_clean_ecc',data,error=2)
  run('spl_tpl_single_data_bit_corrected',fault=lambda p:p[3].__setitem__(20,p[3][20]^1))
  assert cases['spl_tpl_single_data_bit_corrected']['spl_corrected_steps']==1
  run('spl_tpl_single_ecc_bit_tolerated',fault=lambda p:p[3].__setitem__(2048+40,p[3][2048+40]^1))
  assert cases['spl_tpl_single_ecc_bit_tolerated']['spl_corrected_steps']==1
  run('spl_tpl_double_data_bit_rejected',fault=lambda p:p[3].__setitem__(20,p[3][20]^3),error=6)
  def two_steps(p):
   p[3][20]^=1;p[3][276]^=1
  run('spl_tpl_two_steps_corrected',fault=two_steps)
  assert cases['spl_tpl_two_steps_corrected']['spl_corrected_steps']==2
  run('spl_erased_padding_bit_corrected',fault=lambda p:p[14].__setitem__(20,p[14][20]^1))
  assert cases['spl_erased_padding_bit_corrected']['spl_corrected_steps']==1
  run('spl_busy_timeout',mode='spl_timeout',error=1)
  run('ddr_lock_timeout',mode='ddr_timeout',error=3)
  run('ebu_power_timeout',mode='pmu_timeout',error=1)
  run('unsupported_id',mode='wrong_id',error=4)
  for block,page in [(0,0),(0,1),(1,0)]:
   run('bad_block_%d_page_%d'%(block,page),fault=lambda p,b=block,q=page:p[b*64+q].__setitem__(2048,0),error=5)
  run('main_single_bit_corrected',fault=lambda p:p[17].__setitem__(100,p[17][100]^1))
  run('main_double_bit_rejected',fault=lambda p:p[17].__setitem__(100,p[17][100]^3),error=6)
  data=bytearray(original);data[0x8004]^=1;run('header_crc_reject',data,error=7)
  data=bytearray(original);struct.pack_into('>I',data,0x800c,0xffffffff);header_crc(data);run('partition_length_reject',data,error=11)
  data=bytearray(original);data[0x8090]^=1;run('payload_crc_reject',data,error=8)
  data=bytearray(original);struct.pack_into('>I',data,0x8010,0xa0110000);header_crc(data);run('wrong_load_reject',data,error=7)
  data=bytearray(original);data[0x8040+17]^=1;payload_crc(data);run('lzop_flags_reject',data,error=9)
  data=bytearray(original);struct.pack_into('>I',data,0x8040+38,0x100001);payload_crc(data);run('lzop_output_limit_reject',data,error=9)
  data=bytearray(original);data[0x8040+46]^=1;payload_crc(data);run('decoded_adler_reject',data,error=10)
  data=bytearray(original);data[0x8040+50]=0xff;payload_crc(data);run('invalid_lzo_tokens_reject',data,error=10)
  run('measured_uart_registers_synthetic_rom_copy',profile='rom_uart_register_fixture')
  run('region0_already_disabled',mode='already_disabled')
  run('region0_preserve_nondefault_bits',mode='nondefault_addrsel0')
  run('tpl_region0_reenabled',mode='tpl_region0_reenabled')
  for stage in ('spl','tpl'):
   for fault,error in [('fpim_gated',18),('ebu_gated',18),('clc_disabled',18),
                       ('prewrite_changed',17),('remap_stuck',19),('remap_other_changed',19)]:
    mode=stage+'_'+fault;run(mode,mode=mode,error=error)
    r=cases[mode]
    assert r['ddr_starts']==0 and r['clock_update'] is False,'Guard failure started SoC init'
    assert r['pages_read']==([] if stage=='spl' else list(range(1,16)))
    assert 0x90 not in r['nand_commands'],'NAND setup after guard failure'
    assert r['error_stage']==('spl' if stage=='spl' else 'tpl_ebu')
    last=r['ebu_guards'][-1]
    assert last['status']==error
    if fault in ('fpim_gated','ebu_gated','clc_disabled','prewrite_changed'):
     assert not any(v[0]=='write' for v in last['io'])
  run('already_clock_target',mode='already_clock_target')
  run('uart_state_ignored',mode='uart_garbage')
  for mode,error in [('unknown_clock',13),('pll_unlocked',21),('clock_readback',13),('pll_changed',13),
                     ('dll_flicker',3),('invalid_bank',15),('invalid_row',15),('invalid_col',15),
                     ('alias_zero',16),('boundary_fault',16),('memory_before',12),('memory_after',12),
                     ('invalid_capacity',14),('retention',8),('clock_after_decode',13)]:
   run(mode,mode=mode,error=error)
   if mode not in ('memory_after','retention','clock_after_decode'):
    assert 0x90 not in cases[mode]['nand_commands'],'NAND setup after early failure'
  altered=bytearray(expected);altered[100]^=1
  packed=bytearray(subprocess.run(['lzop','-9','-c'],input=bytes(altered),stdout=subprocess.PIPE,check=True).stdout)
  # lzop stdin still records wall-clock mtime. Pin the negative fixture's
  # metadata too, or retention CRC report words vary between clean runs.
  assert packed[:11]==bytes.fromhex('894c5a4f000d0a1a0a1040')
  assert struct.unpack_from('>I',packed,17)[0]==0x0300000d and packed[33]==0
  struct.pack_into('>III',packed,21,0o100644,1791504000,0)
  struct.pack_into('>I',packed,34,zlib.adler32(packed[9:34]))
  h=bytearray(original[0x8000:0x8040]);struct.pack_into('>I',h,12,len(packed));struct.pack_into('>I',h,24,zlib.crc32(packed));h[4:8]=bytes(4);struct.pack_into('>I',h,4,zlib.crc32(h))
  run('build_bound_main_crc',image=original[:0x8000]+bytes(h)+packed,error=28)
  run('four_bank_fourteen_rows',banks=4,rows=14)
  run('controller_256m_subset_main',banks=8,rows=14)
  run('controller_below_minimum',banks=4,rows=13,error=14)
  assert 0x90 not in cases['controller_below_minimum']['nand_commands']
 report={'scope':'Actual linked SPL/TPL instruction chain; ROM initial copy and hardware modeled',
  'model_limitations':['DDR timing/analogue behavior','PLL1 erratum','physical ECC/OOB programming',
   'ROM boot and first-page ECC','cache data/tag physics','NAND bad-block recovery'],
  'coldboot_hardware_verified':False,'hardware_access':False,'passed':len(cases),'cases':cases,
  'measured_uart_register_fixture_sha256':CONTEXT_SHA,
  'nand_rom_entry_register_state_measured':False,
  'measured_romddr1_fixture_sha256':COLD_SHA,
  'chip_geometry_independent_of_probe_answers':True,
  'trace_address':'be22fa00','spl_ecc_error_status_preserved':True,
  'uart_state_accessed':False,'initialization_functions_skipped':False,
  'initial_copy_with_uart_registers_is_synthetic_combination':True,
  'unicorn_wheel_sha256':WHEEL_SHA}
 (out/'mips-tests.json').write_text(json.dumps(report,indent=2)+'\n')
 print('PASS: %d source-built early-chain MIPS cases; hardware coldboot unverified'%len(cases))
if __name__=='__main__':main()
