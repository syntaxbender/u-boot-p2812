// SPDX-License-Identifier: GPL-2.0+
/* Experimental P-2812HNU-F1 RAM board; persistent environment absent. */
#include <command.h>
#include <init.h>
#include <stdio.h>
#include <time.h>
#include <asm/global_data.h>
#include <asm/mipsregs.h>
#include <linux/sizes.h>
#include <mach/vrx200.h>

DECLARE_GLOBAL_DATA_PTR;

int dram_init(void)
{
	/* Board RAM was initialized by the proven UART GCT DDR records. */
	gd->ram_size = SZ_128M;
	return 0;
}

int checkboard(void)
{
	puts("Board: Zyxel P-2812HNU-F1 - experimental RAM-only port\n");
	printf("Clocks: CPU %lu Hz, FPI %lu Hz, CP0 Count %lu Hz\n",
	       ltq_vrx200_cpu_rate(), ltq_vrx200_bus_rate(), get_tbclk());
	return 0;
}

int board_early_init_r(void)
{
	if (IS_ENABLED(CONFIG_NAND_LANTIQ_RO))
		puts("RAM prototype: NAND explicit-probe/read-only; persistent env absent\n");
	else
		puts("RAM prototype: NAND/flash drivers and persistent env absent\n");
	return 0;
}

static int do_ltqinfo(struct cmd_tbl *cmdtp, int flag, int argc,
		      char *const argv[])
{
	printf("CONFIG0=%08x K0_CCA=%u L1I_LINE=%u L1D_LINE=%u\n",
	       read_c0_config(), read_c0_config() & CONF_CM_CMASK,
	       gd->arch.l1i_line_size, gd->arch.l1d_line_size);
	printf("CGU_SYS=%08x CPU=%lu FPI=%lu CP0_HZ=%lu COUNT=%u\n",
	       ltq_vrx200_clock_config(), ltq_vrx200_cpu_rate(),
	       ltq_vrx200_bus_rate(), get_tbclk(), read_c0_count());
	printf("RAM=%lu bytes RELOC=%08lx SP=%08lx\n",
	       (ulong)gd->ram_size, gd->relocaddr, gd->start_addr_sp);
	return 0;
}

U_BOOT_CMD(ltqinfo, 1, 1, do_ltqinfo,
	   "show RAM prototype clocks and relocation (read only)", "");
