/* SPDX-License-Identifier: GPL-2.0+
 * Split from ROMDDR1's hardware-tested SYS/FSR/update/readback path.
 * No UART assumption, PLL restart or reset. NAND-ROM entry remains unmeasured.
 */
#include "stats.h"
#define CGU 0xbf103000u
int clock_prepare(void) {
 u32 i;int rc=0;
 C[0]=0x4e534334;C[1]=1;C[2]=1;
 C[4]=rd32(CGU+0xc);C[5]=rd32(CGU+0x10);
 C[6]=rd32(CGU+4);C[7]=rd32(CGU+8);C[8]=rd32(CGU+0x60);
 if(!(C[7]&2)){rc=E_PLL;goto done;}
 if(C[4]!=0x12||C[5]!=0x03010000){
  if(C[4]!=0x90||C[5]!=0x00020000){rc=E_CLOCK;goto done;}
  C[2]=2;wr32(CGU+0xc,0x12);wr32(CGU+0x10,0x03010000);wr32(CGU+0x20,1);C[9]=1;
 }
 for(i=0;i<WAIT_POLLS;i++){
  C[10]++;if(rd32(CGU+0xc)==0x12&&rd32(CGU+0x10)==0x03010000)break;
 }
 C[11]=rd32(CGU+0xc);C[12]=rd32(CGU+0x10);
 C[13]=rd32(CGU+4);C[14]=rd32(CGU+8);C[15]=rd32(CGU+0x60);
 if(i==WAIT_POLLS||C[13]!=C[6]||C[14]!=C[7]||C[15]!=C[8]){rc=E_CLOCK;goto done;}
 C[2]=3;
done:
 C[3]=(u32)rc;C[31]=0x444f4e45;return rc;
}
