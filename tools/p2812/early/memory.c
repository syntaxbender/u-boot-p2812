/* SPDX-License-Identifier: GPL-2.0+ */
#include "stats.h"
#define CCR(n) (0xbf401000u+(n)*16)
#define TEST_BASE 0xa0400000u
u32 memory_capacity(void) {
 u32 banks=(rd32(CCR(3))&1)?8:4,cs=(rd32(CCR(8))>>24)&3;
 u32 col=(rd32(CCR(15))>>24)&15,row=rd32(CCR(16))&15,c11=rd32(CCR(11));
 u32 cr=(c11>>24)&7,rr=c11&7;
 if(cs!=1||col<=cr||row<=rr)return 0;
 col-=cr;row-=rr;if(col+row>24)return 0;
 return (1u<<(col+row))*banks*2u;
}
int memory_check(void) {
 volatile u32 *p=(void *)TEST_BASE;u32 pass,i,v;
 for(pass=0;pass<4;pass++){
  for(i=0;i<0x4000;i++)p[i]=pass==0?0:pass==1?~0u:pass==2?TEST_BASE+i*4:~(TEST_BASE+i*4);
  for(i=0;i<0x4000;i++){
   v=pass==0?0:pass==1?~0u:pass==2?TEST_BASE+i*4:~(TEST_BASE+i*4);
   if(p[i]!=v)return E_MEMORY;
   B[B_MEMORY_WORDS]++;
  }
 }
 return 0;
}
