/* SPDX-License-Identifier: GPL-2.0+
 * Historical VRX200 non-NOR ADDRSEL0.REGEN prerequisite, exercised by
 * ROMSRAM3/4 in ROM UART SRAM. NAND-ROM inherited state remains modeled.
 * Preserve all region1 timing/mapping and GPIO/NAND control registers.
 */
#include "early.h"
int ebu_prepare(void) {
 u32 before,target;
 /* FPIM and EBU must already be accessible. Never enable a clock here.
  * CLC.DISS bit1 is the conservative filter inherited from ROMSRAM4. */
 if(rd32(0xbf102020u)&0x4400u)return E_ACCESS;
 if(rd32(EBU)&2u)return E_ACCESS;
 before=rd32(EBU+0x20);target=before&~1u;
 if(rd32(EBU+0x20)!=before)return E_STATE;
 if(before&1u)wr32(EBU+0x20,target);
 if(rd32(EBU+0x20)!=target)return E_REMAP;
 return 0;
}
