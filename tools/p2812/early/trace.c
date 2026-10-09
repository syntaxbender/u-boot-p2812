/* SPDX-License-Identifier: GPL-2.0+ */
#include "trace.h"
static const u32 addresses[]={0xa0000000u,0xa0000200u,0xa0000400u,
                             0xa0002000u,0xa4000000u};
static void barrier(void) { __asm__ volatile("sync" ::: "memory"); }
static u32 token(u32 pass,u32 index) {
 return (0x13579000u^(index*0x111u))^(pass?~0u:0);
}
static void store(u32 a,u32 v) { wr32(a,v);barrier(); }
static u32 observed(u32 a,u32 index) {
 u32 v;barrier();v=rd32(a);T[index]=v;T[11]++;return v;
}
int trace_alias(u32 ram) {
 u32 pass,i,index,boundary;int mismatch;
 T[2]=5;T[13]=ram;
 if(ram!=0x08000000u && ram!=0x10000000u)return E_PROBE;
 for(pass=0;pass<2;pass++) {
  index=16+pass*11;mismatch=0;
  for(i=0;i<5;i++)store(addresses[i],token(pass,i));
  for(i=0;i<5;i++)if(observed(addresses[i],index+i)!=token(pass,i))mismatch=1;
  /* At128MiB this historical probe address must alias the base word;
   * at256MiB it must be distinct. Neither outcome is assumed from a banner. */
  boundary=0x2468abcdu^(pass?~0u:0);store(0xa8000000u,boundary);
  for(i=0;i<5;i++) {
   u32 expected=(i==0 && ram==0x08000000u)?boundary:token(pass,i);
   if(observed(addresses[i],index+5+i)!=expected)mismatch=1;
  }
  if(observed(0xa8000000u,index+10)!=boundary)mismatch=1;
  if(mismatch)return E_ALIAS;
  T[12]++;
 }
 T[2]=6;return 0;
}
