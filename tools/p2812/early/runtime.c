/* SPDX-License-Identifier: GPL-2.0+ */
#include "early.h"
void *memcpy(void *d,const void *s,size_t n) {
 u8 *a=d; const u8 *b=s; while(n--) *a++=*b++; return d;
}
void *memset(void *d,int v,size_t n) {
 u8 *a=d; while(n--) *a++=(u8)v; return d;
}
u32 crc32_bytes(const u8 *p,u32 n) {
 u32 c=~0u,i; while(n--) {c^=*p++; for(i=0;i<8;i++) c=(c>>1)^(0xedb88320u&-(c&1));} return ~c;
}
u32 adler32_bytes(const u8 *p,u32 n) {
 u32 a=1,b=0; while(n--) {a+=*p++; if(a>=65521)a-=65521; b+=a;if(b>=65521)b-=65521;} return (b<<16)|a;
}
int wait_bits(u32 a,u32 mask,u32 target) {
 u32 n=WAIT_POLLS; do {if((rd32(a)&mask)==target)return 0;}while(--n); return E_TIMEOUT;
}
void nand_release(void) { wr32(EBU+0xb0,rd32(EBU+0xb0)&~0x100001u); }
/* Only NAND READ0/READSTART and address cycles exist in this reader.
 * Bootstrap inherits the ROM EBU setup, as in the historical small SPL.
 * Four address cycles: 2 column + 2 row, fixed 128 MiB x8 geometry.
 */
int nand_raw_page(u32 page,u8 *dst,u32 length) {
 u32 i; const u8 addr[4]={0,0,(u8)page,(u8)(page>>8)};
 if(page>=65536 || length>2112)return E_BOUNDS;
 wr32(EBU+0xb0,rd32(EBU+0xb0)|0x100001u);
 wr8(NAND|0x18,0x00);
 if(wait_bits(EBU+0xb4,8,8))goto timeout;
 for(i=0;i<4;i++) {wr8(NAND|0x14,addr[i]);if(wait_bits(EBU+0xb4,8,8))goto timeout;}
 wr8(NAND|0x18,0x30);
 if(wait_bits(EBU+0xb4,8,8)||wait_bits(EBU+0xb4,1,1))goto timeout;
 for(i=0;i<length;i++)dst[i]=rd8(NAND|0x10);
 nand_release();return 0;
timeout:
 nand_release();return E_TIMEOUT;
}
