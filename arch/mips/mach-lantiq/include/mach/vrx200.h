/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef __LANTIQ_VRX200_RAM_H
#define __LANTIQ_VRX200_RAM_H

/* Physical SoC register addresses; accesses use uncached KSEG1. */
#define LTQ_VRX200_CGU_SYS	0x1f10300c
#define LTQ_VRX200_ASC1		0x1e100c00

unsigned long ltq_vrx200_cpu_rate(void);
unsigned long ltq_vrx200_bus_rate(void);
unsigned int ltq_vrx200_clock_config(void);

#endif
