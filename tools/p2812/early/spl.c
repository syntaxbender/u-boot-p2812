/* SPDX-License-Identifier: GPL-2.0+
 * Source-built ROM-loaded bootstrap, based on the 2015 Lantiq small SPL
 * page-1 -> SRAM strategy. No DDR, data programming, erase or reset.
 * TPL main+OOB is read and corrected with upstream Hamming 1/256 before
 * the build-bound CRC gate. ROM first-page ECC behaviour is not emulated.
 */
#include "early.h"
#include "generated.h"
#include <linux/mtd/nand_ecc.h>
void spl_fail(u32 code) __attribute__((noreturn,noinline,noclone));
void spl_fail(u32 code) { wr32(SPL_ERROR,code);for(;;)__asm__ volatile("nop"); }
void spl_main(void) {
 u32 i,step,corrected=0;u8 calc[3],*page;int rc;u8 *dst=(u8 *)TPL_BASE;
 wr32(SPL_CORRECTED,0);wr32(SPL_ERROR,0);
 /* Guard before the first read; retain the ROM's NAND timing and pins. */
 rc=ebu_prepare();if(rc)spl_fail((u32)rc);
 for(i=0;i<TPL_BYTES/PAGE_SIZE;i++) {
  page=dst+i*PAGE_SIZE;
  /* OOB temporarily occupies the next 64 bytes, overwritten by the next
   * page. Final spill BE228000..BE22803F is inside SRAM, below the stack.
   * TPL execution/BSS clearing starts only after all pages and CRC pass. */
  if(nand_raw_page(i+1,page,PAGE_SIZE+OOB_SIZE))spl_fail(E_TIMEOUT);
  if(i==0 && page[PAGE_SIZE]!=0xff)spl_fail(E_BADBLOCK);
  for(step=0;step<8;step++) {
   nand_calculate_ecc(0,page+step*256,calc);
   rc=nand_correct_data(0,page+step*256,page+PAGE_SIZE+40+step*3,calc);
   if(rc<0)spl_fail(E_ECC);
   corrected+=(u32)rc;
  }
  wr32(SPL_CORRECTED,corrected);
 }
 if(crc32_bytes(dst,TPL_BYTES)!=TPL_CRC)spl_fail(E_TPL_CRC);
 ((void (*)(void))TPL_BASE)();
 spl_fail(E_TPL_CRC);
}
