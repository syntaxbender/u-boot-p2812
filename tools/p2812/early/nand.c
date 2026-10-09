/* SPDX-License-Identifier: GPL-2.0+
 * Register setup adapted from the tested modern Lantiq NAND RO port and
 * Copyright (C) 2011-2013 Daniel Schwierzeck's Lantiq NAND driver.
 * Fixed Samsung ec:f1:00:95:40 geometry; no write/erase/reset commands.
 * Fixed physical boot partition: bad blocks cause failure, never a read
 * from the adjacent environment/kernel partitions. ROM block is required
 * good; no unproven ROM bad-block skip behaviour is assumed.
 */
#include "early.h"
#include <linux/mtd/nand_ecc.h>
volatile u32 early_corrected;
static u8 pagebuf[2112];
static void gpio_update(u32 off,u32 mask,int set) {
 u32 a=0xbe100b00u+off,v=rd32(a);wr32(a,set?v|mask:v&~mask);
}
static void pin(u32 p,int output) {
 u32 dir,a0,a1,od,mask=1u<<(p%16),base=0x10+(p/16)*0x30;
 if(p>=48){dir=0xa8;a0=0xac;a1=0x64;od=0x34;}
 else {dir=base+8;a0=base+12;a1=base+16;od=base+20;}
 gpio_update(a0,mask,1);gpio_update(a1,mask,0);
 gpio_update(od,mask,output);gpio_update(dir,mask,output);
}
int nand_setup(void) {
 static const u8 expected[5]={0xec,0xf1,0,0x95,0x40};u32 i;
 wr32(0xbf10201c,rd32(0xbf10201c)&~(1u<<10));
 if(wait_bits(0xbf102020,1u<<10,0))return E_TIMEOUT;
 pin(23,1);pin(24,1);pin(13,1);pin(48,0);pin(49,1);
 wr32(EBU+0x24,0x14000031);wr32(EBU+0x64,0x0040d295);
 wr32(EBU+0xb0,0x001005f3);
 wr8(NAND|0x18,0x90);
 if(wait_bits(EBU+0xb4,8,8))goto timeout;
 wr8(NAND|0x14,0);
 if(wait_bits(EBU+0xb4,8,8))goto timeout;
 for(i=0;i<5;i++)if(rd8(NAND|0x10)!=expected[i]){
  nand_release();return E_GEOMETRY;
 }
 nand_release();return 0;
timeout:
 nand_release();return E_TIMEOUT;
}
static int check_block(u32 block) {
 u32 i;int rc;
 for(i=0;i<2;i++){
  rc=nand_raw_page(block*64+i,pagebuf,2112);if(rc)return rc;
  if(pagebuf[2048]!=0xff)return E_BADBLOCK;
 }
 return 0;
}
int nand_page_ecc(u32 page,u8 *dst) {
 u8 calc[3];u32 step;int rc;
 if(page>=PART_END/PAGE_SIZE)return E_BOUNDS;
 rc=nand_raw_page(page,pagebuf,2112);if(rc)return rc;
 for(step=0;step<8;step++){
  nand_calculate_ecc(0,pagebuf+step*256,calc);
  rc=nand_correct_data(0,pagebuf+step*256,pagebuf+2048+40+step*3,calc);
  if(rc<0)return E_ECC;
  early_corrected+=(u32)rc;
 }
 memcpy(dst,pagebuf,2048);return 0;
}
int nand_read_exact(u32 offset,u32 bytes,u8 *dst) {
 /* Page-aligned callers only; last page copies exactly the requested
  * bytes. Scratch is disjoint from the corrected page ECC scratch. */
 u8 data[2048];u32 n,block=~0u;int rc;
 if(offset<MAIN_OFFSET || offset%PAGE_SIZE || bytes>PART_END-offset ||
    offset>=PART_END)return E_BOUNDS;
 while(bytes){
  if(offset/0x20000!=block){
   block=offset/0x20000;rc=check_block(block);if(rc)return rc;
  }
  rc=nand_page_ecc(offset/PAGE_SIZE,data);if(rc)return rc;
  n=bytes<PAGE_SIZE?bytes:PAGE_SIZE;memcpy(dst,data,n);
  offset+=PAGE_SIZE;dst+=n;bytes-=n;
 }
 return 0;
}
