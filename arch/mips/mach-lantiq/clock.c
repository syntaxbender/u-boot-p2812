// SPDX-License-Identifier: GPL-2.0+
/*
 * VRX200 clock decoding from the OpenWrt U-Boot 2013.10 Lantiq port.
 * Copyright (C) 2010 Lantiq Deutschland GmbH
 * Copyright (C) 2011-2013 Daniel Schwierzeck
 * RAM prototype: read clocks only; never program CGU/PLL registers.
 */
#include <asm/addrspace.h>
#include <asm/io.h>
#include <mach/vrx200.h>

unsigned int ltq_vrx200_clock_config(void)
{
	return __raw_readl((void *)CKSEG1ADDR(LTQ_VRX200_CGU_SYS));
}

unsigned long ltq_vrx200_cpu_rate(void)
{
	switch ((ltq_vrx200_clock_config() >> 4) & 0xf) {
	case 0: return 600000000;
	case 1: return 500000000;
	case 2: return 393219000;
	case 3: return 333333333;
	case 5:
	case 6: return 196609500;
	case 7: return 166666667;
	case 4:
	case 8:
	case 9: return 125000000;
	default: return 0;
	}
}

unsigned long ltq_vrx200_bus_rate(void)
{
	unsigned long cpu = ltq_vrx200_cpu_rate();

	switch (ltq_vrx200_clock_config() & 3) {
	case 0: return cpu;
	case 2: return cpu / 2;
	case 3: return cpu * 2 / 5;
	default: return 0;
	}
}

/* CP0 Count advances at half the CPU clock on the VRX288/34K core. */
unsigned long get_tbclk(void)
{
	return ltq_vrx200_cpu_rate() / 2;
}
