/* SPDX-License-Identifier: GPL-2.0+
 * VRX200 clock/DDR sequence adapted from cgu_init.S/mem_init.S,
 * Copyright (C) 2010 Lantiq Deutschland GmbH
 * Copyright (C) 2011-2013 Daniel Schwierzeck.
 * Board DDR constants Copyright (C) 2013 Luka Perkov (vendor header).
 * Diagnostic fork: record raw probes; reject out-of-range observations.
 * DDR start/poll ordering is retained, not claimed to fix the variation.
 * DDR geometry probing retains the historical alias tests. This is NOT
 * a warm RAM test entry: it reinitialises DDR and destroys probe words.
 */
#include "early.h"
#include "vendor/ddr_settings.h"
#include "trace.h"
#define CGU 0xbf103000u
#define MC 0xbf401000u
#define CCR(n) (MC+(n)*16)
static const u32 ddr[]={
 MC_CCR00_VALUE,MC_CCR01_VALUE,MC_CCR02_VALUE,MC_CCR03_VALUE,
 MC_CCR04_VALUE,MC_CCR05_VALUE,MC_CCR06_VALUE,MC_CCR07_VALUE,
 MC_CCR08_VALUE,MC_CCR09_VALUE,MC_CCR10_VALUE,MC_CCR11_VALUE,
 MC_CCR12_VALUE,MC_CCR13_VALUE,MC_CCR14_VALUE,MC_CCR15_VALUE,
 MC_CCR16_VALUE,MC_CCR17_VALUE,MC_CCR18_VALUE,MC_CCR19_VALUE,
 MC_CCR20_VALUE,MC_CCR21_VALUE,MC_CCR22_VALUE,MC_CCR23_VALUE,
 MC_CCR24_VALUE,MC_CCR25_VALUE,MC_CCR26_VALUE,MC_CCR27_VALUE,
 MC_CCR28_VALUE,MC_CCR29_VALUE,MC_CCR30_VALUE,MC_CCR31_VALUE,
 MC_CCR32_VALUE,MC_CCR33_VALUE,MC_CCR34_VALUE,MC_CCR35_VALUE,
 MC_CCR36_VALUE,MC_CCR37_VALUE,MC_CCR38_VALUE,MC_CCR39_VALUE,
 MC_CCR40_VALUE,MC_CCR41_VALUE,MC_CCR42_VALUE,MC_CCR43_VALUE,
 MC_CCR44_VALUE,MC_CCR45_VALUE,MC_CCR46_VALUE,MC_CCR47_VALUE,
 MC_CCR48_VALUE,MC_CCR49_VALUE,MC_CCR50_VALUE,MC_CCR51_VALUE,
 MC_CCR52_VALUE,MC_CCR53_VALUE,MC_CCR54_VALUE,MC_CCR55_VALUE,
 MC_CCR56_VALUE,MC_CCR57_VALUE,MC_CCR58_VALUE,MC_CCR59_VALUE,
 MC_CCR60_VALUE,MC_CCR61_VALUE};
static int ddr_start(void) {
 wr32(CCR(7),rd32(CCR(7))|0x100);
 if(wait_bits(CCR(47),1,1)||wait_bits(CCR(48),1,1))return E_DDR;
 return 0;
}
int soc_init(void) {
 u32 i,c3=MC_CCR03_VALUE,c11=MC_CCR11_VALUE;
 /* Match the historical 500 MHz CPU / 250 MHz DDR config; no reset. */
 if(rd32(CGU+0xc)!=0x12 || rd32(CGU+0x10)!=0x03010000) {
  return E_CLOCK; /* clock.c owns the target; DDR never reprograms CGU. */
 }
 wr32(CCR(7),0);
 for(i=0;i<62;i++)if(i<47||i>51)wr32(CCR(i),ddr[i]);
 wr32(0xbf400430,4); /* Disable FPI bursts, retain AHB bursts. */
 if(ddr_start())return E_DDR;
 T[4]=rd32(CCR(47));T[5]=rd32(CCR(48));
 if(!(T[4]&1) || !(T[5]&1))return E_DDR;
 T[2]=1;
 /* Historical 8-bank / row / column alias probe. */
 wr32(0xa0000000,1);wr32(0xa0002000,0);
 T[6]=rd32(0xa0000000);
 if(T[6]>1)return E_PROBE;
 if(!T[6])c3&=~1u;
 T[2]=2;
 wr32(0xa0000000,0);wr32(0xa8000000,1);wr32(0xa4000000,2);
 T[7]=rd32(0xa0000000);
 if(T[7]>2)return E_PROBE;
 c11+=T[7];T[2]=3;
 wr32(0xa0000000,0);wr32(0xa0000400,1);wr32(0xa0000200,2);
 T[8]=rd32(0xa0000000);
 if(T[8]>2)return E_PROBE;
 c11+=T[8]<<24;T[9]=c3;T[10]=c11;T[2]=4;
 wr32(CCR(7),0);wr32(CCR(3),c3);wr32(CCR(11),c11);
 return ddr_start();
}
