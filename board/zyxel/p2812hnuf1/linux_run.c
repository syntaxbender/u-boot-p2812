// SPDX-License-Identifier: GPL-2.0+
/* Manual fixed-layout Linux handoff. NAND access remains read-only. */
#include <bootm.h>
#include <command.h>
#include <console.h>
#include <image.h>
#include <irq_func.h>
#include <lmb.h>
#include <linux/errno.h>
#include <linux/sizes.h>
#include <stdio.h>
#include <asm/addrspace.h>
#include <asm/global_data.h>
#include <asm/mipsregs.h>
#include <mach/p2812_linux.h>
#include "nand_kernel.h"

DECLARE_GLOBAL_DATA_PTR;

#define KERNEL_LOAD 0x80002000UL
#define KERNEL_INPUT 0xa2000000UL

/* Record ownership at the allocation site, independently of mutable images.
 * A failed release is retained and prevents a later attempt hiding the leak.
 */
static struct {
	phys_addr_t address;
	phys_size_t size;
	bool owned;
	bool tracking;
	bool duplicate;
} kernel_reservation;

void p2812_linux_lmb_record(phys_addr_t address, phys_size_t size)
{
	if (!kernel_reservation.tracking)
		return;
	if (kernel_reservation.owned) {
		kernel_reservation.duplicate = true;
		return;
	}
	kernel_reservation.address = address;
	kernel_reservation.size = size;
	kernel_reservation.owned = true;
}

static int linux_context_ok(void)
{
	if (gd->ram_size < SZ_128M ||
	    (read_c0_config() & CONF_CM_CMASK) != 3)
		return -ENOTSUPP;
	return 0;
}

static int linux_image_ok(u32 declared)
{
	if (images.os.load != KERNEL_LOAD || images.ep != KERNEL_LOAD ||
	    images.os.os != IH_OS_LINUX || images.os.arch != IH_ARCH_MIPS ||
	    images.os.type != IH_TYPE_KERNEL || images.os.comp != IH_COMP_LZMA ||
	    images.rd_start || images.rd_end || images.ft_len ||
	    !declared || declared > CONFIG_SYS_BOOTM_LEN)
		return -EBADMSG;
	return 0;
}

static int linux_run(bool from_nand)
{
	struct bootm_info bmi;
	const char *stage = "unreleased reservation";
	const u8 *size;
	u32 declared;
	ulong iflag = 0;
	int ret = -EBUSY;
	long cleanup;

	if (kernel_reservation.owned) {
		p2812_nand_kernel_invalidate();
		puts("Previous Linux output reservation retained; new attempt blocked\n");
		return CMD_RET_FAILURE;
	}
	kernel_reservation.duplicate = false;
	stage = "CPU/RAM context";
	ret = linux_context_ok();
	if (ret)
		goto error;
	stage = "cancelled";
	ret = -EINTR;
	if (ctrlc())
		goto error;
	stage = from_nand ? "NAND read/verify" : "RAM image verify";
	ret = from_nand ? p2812_nand_kernel_load() : p2812_ram_kernel_verify();
	if (ret)
		goto error;
	size = (const u8 *)KERNEL_INPUT + sizeof(struct legacy_img_hdr) + 5;
	declared = (u32)size[0] | (u32)size[1] << 8 |
		   (u32)size[2] << 16 | (u32)size[3] << 24;
	stage = "cancelled";
	ret = -EINTR;
	if (ctrlc())
		goto error;
	bootm_init(&bmi);
	bmi.addr_img = "a2000000";
	bmi.conf_ramdisk = "-";
	bmi.conf_fdt = NULL;
	bmi.cmd_name = from_nand ? "nandbootrun" : "rambootrun";
	bmi.boot_progress = false;
	stage = "bootm start";
	ret = bootm_run_states(&bmi, BOOTM_STATE_START |
			      BOOTM_STATE_FINDOS | BOOTM_STATE_FINDOTHER);
	if (ret)
		goto error;
	stage = "kernel identity";
	ret = linux_image_ok(declared);
	if (ret)
		goto error;
	stage = "cancelled";
	ret = -EINTR;
	if (ctrlc())
		goto error;
	/* The upstream decoder writes before allocating its output reservation. */
	stage = "free output window";
	ret = -EBUSY;
	if (lmb_get_free_size(CKSEG1ADDR(KERNEL_LOAD)) < CONFIG_SYS_BOOTM_LEN)
		goto error;
	stage = "bootm loados";
	kernel_reservation.tracking = true;
	ret = bootm_run_states(&bmi, BOOTM_STATE_LOADOS);
	kernel_reservation.tracking = false;
	if (ret)
		goto error;
	stage = "decoded identity/reservation";
	ret = linux_image_ok(declared);
	if (ret)
		goto error;
	ret = -EBADMSG;
	if (!kernel_reservation.owned || kernel_reservation.duplicate ||
	    kernel_reservation.address != CKSEG1ADDR(KERNEL_LOAD) ||
	    kernel_reservation.size != declared ||
	    images.os.image_len != declared)
		goto error;
	stage = "cancelled";
	ret = -EINTR;
	if (ctrlc())
		goto error;
	stage = "bootm prep";
	ret = bootm_run_states(&bmi, BOOTM_STATE_OS_PREP);
	if (ret)
		goto error;
	stage = "final CPU/RAM context";
	ret = linux_context_ok();
	if (ret)
		goto error;
	stage = "final kernel identity";
	ret = linux_image_ok(declared);
	if (ret)
		goto error;
	if (images.os.image_len != declared || !kernel_reservation.owned) {
		ret = -EBADMSG;
		goto error;
	}
	stage = "cancelled";
	ret = -EINTR;
	if (ctrlc())
		goto error;
	printf("P2812 Linux handoff: source=%s decoded=%u entry=%08lx\n",
	       from_nand ? "NAND" : "RAM", declared, KERNEL_LOAD);
	stage = "Linux returned";
	/* LOADOS restores its saved interrupt state before returning. Separate
	 * PREP/GO calls therefore need an explicit final interrupt handoff.
	 */
	iflag = disable_interrupts();
	ret = bootm_run_states(&bmi, BOOTM_STATE_OS_GO);
	if (!ret)
		ret = -EIO;
error:
	kernel_reservation.tracking = false;
	if (iflag)
		enable_interrupts();
	if (kernel_reservation.owned) {
		cleanup = lmb_free(kernel_reservation.address,
				   kernel_reservation.size, LMB_NONE);
		if (!cleanup)
			kernel_reservation.owned = false;
		else
			printf("Linux output reservation retained (%ld); new attempts blocked\n",
			       cleanup);
	}
	p2812_nand_kernel_invalidate();
	printf("P2812 Linux boot stopped: %s (%d); image invalidated; staying at console\n",
	       stage, ret);
	return CMD_RET_FAILURE;
}

static int do_nandbootrun(struct cmd_tbl *cmdtp, int flag, int argc,
			  char *const argv[])
{
	if (argc != 1)
		return CMD_RET_USAGE;
	return linux_run(true);
}

static int do_rambootrun(struct cmd_tbl *cmdtp, int flag, int argc,
			 char *const argv[])
{
	if (argc != 1)
		return CMD_RET_USAGE;
	return linux_run(false);
}

U_BOOT_CMD(nandbootrun, 1, 0, do_nandbootrun,
	   "read/verify fixed NAND kernel and execute Linux",
	   "No arguments. Kernel 60000..560000; input a2000000; entry 80002000.\n"
	   "NAND reader is read-only; the Linux kernel may access/write NAND.");
U_BOOT_CMD(rambootrun, 1, 0, do_rambootrun,
	   "verify fixed RAM kernel and execute Linux",
	   "No arguments. Input a2000000, max 16 MiB; entry 80002000.\n"
	   "Does not read NAND; Linux access depends on the supplied kernel/DTB.");
