/* SPDX-License-Identifier: GPL-2.0+ */
#include "early.h"
#include "generated.h"
#include "trace.h"
#include "stats.h"
volatile u32 early_error;
static u32 nand_touched;
void tpl_idle(void) __attribute__((noreturn,noinline,noclone));
void tpl_idle(void) {for(;;)__asm__ volatile("nop");}
static void stop(u32 code) __attribute__((noreturn));
static void stop(u32 code) {early_error=code;B[B_STATUS]=code;T[3]=code;B[31]=0x444f4e45;tpl_idle();}
void ebu_fail(u32 code) __attribute__((noreturn,noinline,noclone));
void ebu_fail(u32 code) {stop(code);}
void fail(u32 code) {if(nand_touched)nand_release();stop(code);}
void tpl_main(void) {
 u8 *image=(u8 *)INPUT_BUFFER;u32 size,written,i;int rc;
 for(i=0;i<32;i++){C[i]=0;B[i]=0;}for(i=0;i<TRACE_WORDS;i++)T[i]=0;
 B[B_MAGIC]=0x4e535234;B[B_VERSION]=1;T[0]=0x4e535234;T[1]=1;B[B_PHASE]=1;
 /* Early failure must not release NAND through an inaccessible EBU. */
 rc=ebu_prepare();if(rc)ebu_fail((u32)rc);
 B[B_PHASE]=2;rc=clock_prepare();if(rc)fail((u32)rc);
 B[B_PHASE]=3;
 rc=soc_init();if(rc)fail(rc);
 B[B_RAM]=memory_capacity();
 if(B[B_RAM]!=0x08000000u&&B[B_RAM]!=0x10000000u)fail(E_RAM_SIZE);
 B[B_PHASE]=4;rc=trace_alias(B[B_RAM]);if(rc)fail((u32)rc);
 rc=memory_check();if(rc)fail((u32)rc);
 B[B_PHASE]=5;nand_touched=1;
 rc=nand_setup();if(rc)fail(rc);
 B[B_PHASE]=6;
 rc=nand_read_exact(MAIN_OFFSET,64,image);if(rc)fail(rc);
 /* Validate the header before trusting its length or reading more NAND. */
 {u32 saved=be32(image+4),c;memset(image+4,0,4);c=crc32_bytes(image,64);
  if(c!=saved || be32(image)!=0x27051956)fail(E_HEADER);
 }
 size=be32(image+12);
 if(size>PART_END-MAIN_OFFSET-64 || size<54)fail(E_BOUNDS);
 B[B_PHASE]=7;B[B_IMAGE_BYTES]=size+64;
 rc=nand_read_exact(MAIN_OFFSET,size+64,image);if(rc)fail(rc);
 B[B_NAND_CORRECTED]=early_corrected;B[B_CRC_BEFORE]=crc32_bytes(image,size+64);
 rc=memory_check();if(rc)fail((u32)rc);
 B[B_CRC_AFTER]=crc32_bytes(image,size+64);
 if(B[B_CRC_BEFORE]!=B[B_CRC_AFTER])fail(E_DATA_CRC);
 B[B_PHASE]=8;
 rc=decode_main(image,size+64,(u8 *)MAIN_DEST,&written);if(rc)fail(rc);
 B[B_MAIN_BYTES]=written;B[B_MAIN_CRC]=crc32_bytes((u8 *)MAIN_DEST,written);
 if(written!=MAIN_BYTES||B[B_MAIN_CRC]!=MAIN_EXPECTED_CRC)fail(E_MAIN_CRC);
 if(rd32(0xbf10300cu)!=0x12||rd32(0xbf103010u)!=0x03010000u)fail(E_CLOCK);
 B[B_PHASE]=9;B[30]=0x4a554d50;B[31]=0x444f4e45;
 handoff();
}
