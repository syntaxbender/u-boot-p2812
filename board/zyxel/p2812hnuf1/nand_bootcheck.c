// SPDX-License-Identifier: GPL-2.0+
/* Interruptible autoboot rehearsal: read, verify and decompress, then stop. */
#include <bootm.h>
#include <command.h>
#include <console.h>
#include <image.h>
#include <lmb.h>
#include <linux/errno.h>
#include <stdio.h>
#include <asm/addrspace.h>
#include "nand_kernel.h"

static int do_nandbootcheck(struct cmd_tbl *cmdtp, int flag, int argc,
			    char *const argv[])
{
	struct bootm_info bmi;
	const char *stage = "cancelled";
	const u8 *size;
	u32 declared;
	ulong actual;
	int ret = -EINTR;
	long cleanup;

	if (argc != 1)
		return CMD_RET_USAGE;
	p2812_nand_kernel_invalidate();
	if (ctrlc())
		goto error;
	stage = "NAND read/verify";
	ret = p2812_nand_kernel_load();
	if (ret)
		goto error;
	/* The reader already validated this LZMA-Alone declared size. */
	size = (const u8 *)0xa2000000UL + sizeof(struct legacy_img_hdr) + 5;
	declared = (u32)size[0] | (u32)size[1] << 8 |
		   (u32)size[2] << 16 | (u32)size[3] << 24;
	stage = "cancelled";
	ret = -EINTR;
	if (ctrlc())
		goto error;
	bootm_init(&bmi);
	bmi.addr_img = "a2000000";
	bmi.conf_ramdisk = "-";
	/* No external FDT selector: the legacy MIPS kernel carries its DTB.
	 * A literal "-" here is parsed as an external address by boot_get_fdt().
	 */
	bmi.conf_fdt = NULL;
	bmi.cmd_name = "nandbootcheck";
	bmi.boot_progress = false;
	stage = "bootm start";
	ret = bootm_run_states(&bmi, BOOTM_STATE_START |
			      BOOTM_STATE_FINDOS | BOOTM_STATE_FINDOTHER);
	if (ret)
		goto error;
	stage = "cancelled";
	ret = -EINTR;
	if (ctrlc())
		goto error;
	/* Decoder writes happen before upstream reserves the output. Require the
	 * full possible output window to be free before touching that RAM.
	 */
	stage = "RAM reservation";
	ret = -EBUSY;
	if (lmb_get_free_size(CKSEG1ADDR(0x80002000UL)) < CONFIG_SYS_BOOTM_LEN)
		goto error;
	stage = "bootm loados";
	ret = bootm_run_states(&bmi, BOOTM_STATE_LOADOS);
	if (ret)
		goto error;
	/* Successful LOADOS owns exactly this reservation. Release it so
	 * a repeated check does not retain kernel memory in the global LMB.
	 * Do not free anything on a failed LOADOS (reservation may not be ours).
	 */
	stage = "decoded bounds";
	actual = images.os.image_len;
	if (images.os.load != 0x80002000UL || !actual ||
	    actual > CONFIG_SYS_BOOTM_LEN) {
		ret = -EFBIG;
		goto error;
	}
	cleanup = lmb_free(CKSEG1ADDR(images.os.load), actual, LMB_NONE);
	if (cleanup) {
		stage = "LMB release";
		ret = cleanup;
		goto error;
	}
	if (actual != declared) {
		stage = "decoded size";
		ret = -EBADMSG;
		goto error;
	}
	/* Invalidate even on success: manual GO must not reuse rehearsal state. */
	p2812_nand_kernel_invalidate();
	printf("NAND boot check passed: decoded=%lu; Linux not executed; image invalidated; staying at console\n",
	       actual);
	return CMD_RET_SUCCESS;
error:
	p2812_nand_kernel_invalidate();
	printf("NAND boot check stopped: %s (%d); image invalidated; staying at console\n",
	       stage, ret);
	return CMD_RET_FAILURE;
}

U_BOOT_CMD(nandbootcheck, 1, 0, do_nandbootcheck,
	   "rehearse fixed NAND kernel boot without executing Linux (read only)",
	   "No arguments. Reads/verifies NAND, runs bootm start and loados only.\n"
	   "On success or error invalidates the image and returns to console.\n"
	   "No automatic retry, reset, Linux execution or persistent modification.");
