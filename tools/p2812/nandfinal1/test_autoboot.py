#!/usr/bin/env python3
"""Execute linked bootdelay/autoboot policy; model console, time and dispatch.

The actual nandbootrun/bootm/LMB path is covered by test_mips.py. This
separate model checks the compiled default environment and its selection.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import struct
import sys
import tempfile
import zipfile
from model_support import prepare, physical, TEXT, RELOC, GD, SENTINEL

WHEEL_SHA='9d6e6dea140560de4ebd8446661f7ef84a357d428c14a3ef09dacd306ec8c239'


def require(ok,message):
    if not ok:raise RuntimeError(message)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('build',type=Path)
    parser.add_argument('--wheel',type=Path,default=Path('/inputs/unicorn.whl'))
    parser.add_argument('--output',type=Path)
    parser.add_argument('--allow-legacy-layout',action='store_true')
    args=parser.parse_args()
    require(platform.system()=='Linux','Native macOS Unicorn prohibited')
    require(hashlib.sha256(args.wheel.read_bytes()).hexdigest()==WHEEL_SHA,'Unicorn wheel differs')
    syms,f,relocated,relocations=prepare(args.build/'main')
    require('cmd_func' in f or args.allow_legacy_layout,'Compiler-derived command layout required')
    def addr(name):return syms[name]+RELOC-TEXT
    dtb=relocated[addr('__dtb_dt_begin')-RELOC:addr('__dtb_dt_end')-RELOC]
    require(dtb[:4]==bytes.fromhex('d00dfeed'),'Embedded control FDT absent')
    require(all(key not in dtb for key in [b'bootdelay\0',b'kernel-offset\0',b'rootdisk-offset\0']),
            'Embedded FDT contains a modeled policy override')
    start=addr('default_environment')-RELOC
    blob=relocated[start:];length=blob.index(b'\0\0')+2;blob=blob[:length]
    env={}
    for value in blob.split(b'\0'):
        if value:
            key,data=value.split(b'=',1);env[key.decode()]=data.decode()
    require(env.get('bootcmd')=='nandbootrun' and env.get('bootdelay')=='5','Compiled default boot policy differs')
    require('bootargs' not in env,'Unexpected built-in Linux command line override')
    cases=[]
    with tempfile.TemporaryDirectory(prefix='nandfinal1-autoboot-') as tmp:
        with zipfile.ZipFile(args.wheel) as wheel:
            require(all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in wheel.namelist()),'Unsafe wheel')
            wheel.extractall(tmp)
        sys.path.insert(0,tmp)
        from unicorn import Uc,UC_ARCH_MIPS,UC_MODE_MIPS32,UC_MODE_BIG_ENDIAN,UC_HOOK_CODE
        import unicorn.mips_const as m
        for name,key_at,dispatch_ret in [('expiry',None,0),('key_immediate',0,0),
                                         ('key_during_countdown',1500,0),('command_failure',None,1)]:
            u=Uc(UC_ARCH_MIPS,UC_MODE_MIPS32|UC_MODE_BIG_ENDIAN)
            u.ctl_set_cpu_model(m.UC_CPU_MIPS32_34KF)
            u.mem_map(physical(RELOC),0x100000);u.mem_map(0x3000000,0x500000)
            u.mem_write(physical(RELOC),relocated);u.reg_write(m.UC_MIPS_REG_K0,GD)
            state=dict(ms=0,returned=False,keys=0,dispatch=[],fdt_defaults=[])
            def text(p):return bytes(u.mem_read(physical(p),2048)).split(b'\0',1)[0].decode('latin1')
            def get32(p):return struct.unpack('>I',u.mem_read(physical(p),4))[0]
            def result(value=0):
                u.reg_write(m.UC_MIPS_REG_V0,value&0xffffffff)
                u.reg_write(m.UC_MIPS_REG_PC,u.reg_read(m.UC_MIPS_REG_RA))
            pointers={};position=addr('default_environment')
            for value in blob.split(b'\0'):
                if value:
                    key,data=value.split(b'=',1);pointers[key.decode()]=position+len(key)+1
                position+=len(value)+1
            table=addr('_u_boot_list_2_cmd_2_nandbootrun')
            require(text(get32(table+f.get('cmd_name',0)))=='nandbootrun','Wrong compiled command table name')
            require(get32(table+f.get('cmd_func',12))==addr('do_nandbootrun'),'Default command callback differs')
            def hook(uc,pc,size,_):
                a0,a1,a2=[uc.reg_read(r) for r in [m.UC_MIPS_REG_A0,m.UC_MIPS_REG_A1,m.UC_MIPS_REG_A2]]
                if pc==SENTINEL:state['returned']=True;uc.emu_stop()
                elif pc==addr('env_get'):result(pointers.get(text(a0),0))
                elif pc==addr('ofnode_conf_read_int'):
                    state['fdt_defaults'].append([text(a0),a1]);result(a1)
                elif pc==addr('get_timer'):result(state['ms']-a0)
                elif pc in [addr(n) for n in ['udelay','__udelay'] if n in syms]:
                    require(0<a0<=10000,'Unbounded policy delay');state['ms']+=(a0+999)//1000;result()
                elif pc==addr('tstc'):result(int(key_at is not None and state['ms']>=key_at and not state['keys']))
                elif pc==addr('getchar'):state['keys']+=1;result(ord(' '))
                elif pc==addr('run_command_list'):
                    require(text(a0)=='nandbootrun' and a1==0xffffffff and a2==0,'Unexpected automatic command')
                    state['dispatch'].append(text(a0));result(dispatch_ret)
                elif pc in [addr(n) for n in ['printf','puts','putc','schedule'] if n in syms]:result()
                elif pc in [addr(n) for n in ['reset_cpu','do_reset'] if n in syms]:
                    raise RuntimeError('Policy reset after command failure')
            names=['env_get','ofnode_conf_read_int','get_timer','udelay','__udelay','tstc','getchar',
                   'run_command_list','printf','puts','putc','schedule','reset_cpu','do_reset']
            for pc in {addr(n) for n in names if n in syms}|{SENTINEL}:
                u.hook_add(UC_HOOK_CODE,hook,begin=pc,end=pc)
            def call(symbol,arg=0):
                state['returned']=False;u.reg_write(m.UC_MIPS_REG_SP,0xa33fe000)
                u.reg_write(m.UC_MIPS_REG_RA,SENTINEL);u.reg_write(m.UC_MIPS_REG_A0,arg)
                u.emu_start(addr(symbol),SENTINEL+4,timeout=10000000,count=1000000)
                require(state['returned'],'Policy failed to return: '+name)
                return u.reg_read(m.UC_MIPS_REG_V0)
            command=call('bootdelay_process');require(text(command)=='nandbootrun','Wrong selected bootcmd')
            require(get32(addr('stored_bootdelay'))==5,'Stored bootdelay differs')
            call('autoboot_command',command)
            if key_at is None:
                require(state['ms']==5000 and state['dispatch']==['nandbootrun'] and state['keys']==0,
                        'Expiry time/dispatch differs')
            else:
                require(not state['dispatch'] and state['keys']==1 and state['ms']==key_at,
                        'Single-key abort failed')
            cases.append(dict(case=name,passed=True,elapsed_model_ms=state['ms'],
                              dispatch=state['dispatch'],keys_consumed=state['keys'],dispatch_return=dispatch_ret))
            print('PASS: autoboot '+name,flush=True)
    report=dict(passed=True,cases_passed=len(cases),cases=cases,compiled_default_environment=env,
                compiler_derived_command_layout='cmd_func' in f,relocation_count=relocations,
                compiled_control_fdt_has_no_policy_overrides=True,
                hardware_access=False,linux_instructions_executed=0,
                real_linked_functions=['bootdelay_process','autoboot_command','abortboot_single_key'],
                modeled_services=['console','timer','default-environment lookup','ofnode config defaults','command dispatcher'],
                limit='Command dispatch is modeled; actual nandbootrun/bootm/LMB executes in the separate32case suite.')
    out=args.output or args.build/'autoboot-tests.json'
    with out.open('x') as stream:stream.write(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
