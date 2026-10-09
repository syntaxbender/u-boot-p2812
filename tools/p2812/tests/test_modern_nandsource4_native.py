#!/usr/bin/env python3
"""Linux ASan/UBSan test of the actual freestanding image/ECC/LZO sources."""
import argparse
import json
from pathlib import Path
import platform
import subprocess
ROOT=Path(__file__).resolve().parents[2]
HARNESS=r'''
#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include "early.h"
#include <linux/lzo.h>
#include <linux/mtd/nand_ecc.h>
int main(int argc,char **argv) {
 FILE *f;long size;u8 *p,*out,*q,original[256],calc[3],gold[3],copy[256];
 u32 got,i,j,state=1234567;size_t cap;int rc;
 assert(argc==2);f=fopen(argv[1],"rb");assert(f);fseek(f,0,SEEK_END);size=ftell(f);rewind(f);
 p=malloc((size_t)size);assert(p);assert(fread(p,1,size,f)==(size_t)size);fclose(f);
 out=malloc(OUT_LIMIT);assert(out);
 assert(decode_main(p,size,out,&got)==0 && got==248744);
 /* Every short header length, and selected actual-payload truncations:
  * allocate exactly the declared input, so ASan catches read overruns. */
 for(i=0;i<160;i++) {
  u32 n=i<96?i:(u32)size-(i-95);
  q=malloc(n?n:1);assert(q);for(j=0;j<n;j++)q[j]=p[j];
  rc=decode_main(q,n,out,&got);assert(rc!=0 && got==0);free(q);
 }
 /* Exercise decoder token bounds directly, independently of outer CRCs. */
 for(i=0;i<5000;i++) {
  u32 n=i%64;q=malloc(n?n:1);assert(q);
  for(j=0;j<n;j++){state=state*1664525u+1013904223u;q[j]=state>>24;}
  cap=128;rc=lzo1x_decompress_safe(q,n,out,&cap);
  assert(rc<=0 && cap<=128);free(q);
 }
 for(i=0;i<256;i++)original[i]=(u8)(i*37+13);
 nand_calculate_ecc(0,original,gold);
 for(i=0;i<2048;i++) {
  for(j=0;j<256;j++)copy[j]=original[j];copy[i/8]^=1u<<(i%8);
  nand_calculate_ecc(0,copy,calc);assert(nand_correct_data(0,copy,gold,calc)==1);
  for(j=0;j<256;j++)assert(copy[j]==original[j]);
 }
 for(i=0;i<2047;i++) {
  for(j=0;j<256;j++)copy[j]=original[j];copy[i/8]^=1u<<(i%8);copy[(i+1)/8]^=1u<<((i+1)%8);
  nand_calculate_ecc(0,copy,calc);assert(nand_correct_data(0,copy,gold,calc)<0);
 }
 free(p);free(out);puts("PASS: valid decode; 160 truncations; 5000 token inputs; 2048 corrected bits; 2047 double-bit rejections; ASan+UBSan");return 0;
}
'''
def main():
 p=argparse.ArgumentParser();p.add_argument('output',type=Path);a=p.parse_args()
 if platform.system()!='Linux':raise RuntimeError('Pinned Linux only')
 out=a.output;src=Path('/tmp/nandsource4');work=out/'native';work.mkdir()
 harness=work/'harness.c';harness.write_text(HARNESS)
 fixture=work/'main.uImage';fixture.write_bytes((out/'package/nandsource4-main-area.img').read_bytes()[0x8000:])
 flags=['gcc','-O1','-g','-fno-omit-frame-pointer','-fsanitize=address,undefined','-fno-sanitize-recover=all',
        '-fno-pie','-no-pie','-ffunction-sections','-fdata-sections','-Wl,--gc-sections',
        '-fno-builtin','-Dmemcpy=early_memcpy','-Dmemset=early_memset',
        '-I'+str(src),'-I'+str(src/'include')]
 subprocess.run(flags+[str(harness),str(src/'image.c'),str(src/'runtime.c'),
  str(src/'vendor/lzo1x_decompress.c'),str(src/'vendor/nand_ecc.c'),'-o',str(work/'test')],check=True)
 subprocess.run([str(work/'test'),str(fixture)],check=True)
 # Shared native ECC used only to generate synthetic OOB in the MIPS MMIO
 # tests. NAND reads and corrections themselves execute linked MIPS code.
 subprocess.run(['gcc','-shared','-fPIC','-O2','-I'+str(src),'-I'+str(src/'include'),
  str(src/'vendor/nand_ecc.c'),'-o',str(work/'ecc.so')],check=True)
 report={'passed':True,'sanitizers':['AddressSanitizer','UndefinedBehaviorSanitizer'],
  'valid_decode':1,'truncations':160,'decoder_token_inputs':5000,
  'single_bits_corrected':2048,'adjacent_double_bits_rejected':2047,'hardware_access':False}
 (out/'native-tests.json').write_text(json.dumps(report,indent=2)+'\n')
if __name__=='__main__':main()
