// SPDX-License-Identifier: GPL-2.0+
/*
 * Experimental read-only VRX200 EBU NAND port for P-2812HNU-F1.
 * Register setup adapted from Daniel Schwierzeck's U-Boot Lantiq port
 * (Copyright 2011-2013 Daniel Schwierzeck). NAND core/ECC are upstream.
 * No automatic probe: EBU/GPIO access begins only with an ltqnand command.
 */
#include <asm/io.h>
#include <linux/bitops.h>
#include <linux/errno.h>
#include <linux/mtd/rawnand.h>
#include <linux/string.h>
#include <nand.h>
#include <stdio.h>
#include <time.h>
#include <mach/nand_ro.h>

#define LTQ_EBU		0xbe105300UL
#define LTQ_GPIO		0xbe100b00UL
#define LTQ_NAND		0xb4000000UL
#define PMU_PWDCR	0xbf10201cUL
#define PMU_STATUS	0xbf102020UL
#define PMU_EBU		BIT(10)
#define EBU_ADDSEL1	0x24
#define EBU_BUSCON1	0x64
#define EBU_NAND_CON	0xb0
#define EBU_NAND_WAIT	0xb4
#define NANDM		BIT(0)
#define LATCH_CS		BIT(20)
#define CMD_CS		BIT(4)
#define CMD_CLE		BIT(3)
#define CMD_ALE		BIT(2)
#define WAIT_READY	BIT(0)
#define WAIT_COMPLETE	BIT(3)
#define CYCLE_TIMEOUT_MS	20

static struct nand_chip ltq_chip;
static int io_error;
/* A failed probe is latched; retry requires restarting the RAM experiment. */
static int probe_status;
static bool probe_attempted;

static u32 ebu_read(u32 offset)
{
	return __raw_readl((void __iomem *)(LTQ_EBU + offset));
}

static void ebu_write(u32 offset, u32 value)
{
	__raw_writel(value, (void __iomem *)(LTQ_EBU + offset));
}

static void gpio_update(u32 offset, u32 mask, bool set)
{
	void __iomem *reg = (void __iomem *)(LTQ_GPIO + offset);
	u32 value = __raw_readl(reg);

	__raw_writel(set ? value | mask : value & ~mask, reg);
}

static void nand_pin(unsigned int pin, bool output)
{
	u32 dir, alt0, alt1, od;
	u32 mask = BIT(pin % 16);

	/* VRX200 bank 3 has non-contiguous ALTSEL1/OD registers. */
	if (pin >= 48) {
		dir = 0xa8;
		alt0 = 0xac;
		alt1 = 0x64;
		od = 0x34;
	} else {
		u32 base = 0x10 + (pin / 16) * 0x30;

		dir = base + 0x08;
		alt0 = base + 0x0c;
		alt1 = base + 0x10;
		od = base + 0x14;
	}
	gpio_update(alt0, mask, true);
	gpio_update(alt1, mask, false);
	gpio_update(od, mask, output);
	gpio_update(dir, mask, output);
}

static void setup_bus(void)
{
	/* Same five pins as the legacy P2812 board; no PLL/DDR programming. */
	nand_pin(23, true);
	nand_pin(24, true);
	nand_pin(13, true);
	nand_pin(48, false);
	nand_pin(49, true);
	ebu_write(EBU_ADDSEL1, 0x14000031);
	/* Legacy: SETUP|ALEC(3)|BCGEN_INTEL|WR(2)|RD(2)|HOLD(1)|RECOV(1)|MULT4. */
	ebu_write(EBU_BUSCON1, 0x0040d295);
	/* CS1, polarities and mux; hardware ECC remains disabled. */
	ebu_write(EBU_NAND_CON, 0x000005f2);
}

static int enable_ebu(void)
{
	void __iomem *power = (void __iomem *)PMU_PWDCR;
	ulong start;
	u32 value = __raw_readl(power);

	/* Only enable the EBU gate; preserve all other PMU/clock settings. */
	if (value & PMU_EBU)
		__raw_writel(value & ~PMU_EBU, power);
	start = get_timer(0);
	while (__raw_readl((void __iomem *)PMU_STATUS) & PMU_EBU) {
		if (get_timer(start) >= 100)
			return -ETIMEDOUT;
	}
	return 0;
}

static bool readonly_command(int command)
{
	switch (command) {
	case NAND_CMD_READ0:
	case NAND_CMD_READSTART:
	case NAND_CMD_RNDOUT:
	case NAND_CMD_RNDOUTSTART:
	case NAND_CMD_READID:
	case NAND_CMD_STATUS:
	case NAND_CMD_RESET:
	case NAND_CMD_PARAM:
	case NAND_CMD_GET_FEATURES:
		return true;
	default:
		return false;
	}
}

static void wait_cycle(void)
{
	ulong start = get_timer(0);

	while (!(ebu_read(EBU_NAND_WAIT) & WAIT_COMPLETE)) {
		if (get_timer(start) >= CYCLE_TIMEOUT_MS) {
			io_error = -ETIMEDOUT;
			return;
		}
	}
}

static void ro_cmd_ctrl(struct mtd_info *mtd, int command, unsigned int ctrl)
{
	struct nand_chip *chip = mtd_to_nand(mtd);
	ulong address = (ulong)chip->IO_ADDR_W;

	if (ctrl & NAND_CTRL_CHANGE) {
		address &= ~(CMD_ALE | CMD_CLE);
		if (ctrl & NAND_ALE)
			address |= CMD_ALE;
		if (ctrl & NAND_CLE)
			address |= CMD_CLE;
		chip->IO_ADDR_W = (void __iomem *)address;
	}
	if (command == NAND_CMD_NONE || io_error)
		return;
	/* Only command/address bus cycles exist; data writes never reach NAND. */
	if ((address & CMD_CLE) && !(address & CMD_ALE)) {
		if (!readonly_command(command)) {
			io_error = -EROFS;
			return;
		}
	} else if (!(address & CMD_ALE) || (address & CMD_CLE)) {
		io_error = -EROFS;
		return;
	}
	__raw_writeb(command, (void __iomem *)address);
	wait_cycle();
}

static void ro_select_chip(struct mtd_info *mtd, int select)
{
	u32 value = ebu_read(EBU_NAND_CON);

	if (select == 0)
		value |= NANDM | LATCH_CS;
	else
		value &= ~(NANDM | LATCH_CS);
	if (select != 0 && select != -1)
		io_error = -EINVAL;
	ebu_write(EBU_NAND_CON, value);
}

static int ro_dev_ready(struct mtd_info *mtd)
{
	/* Let the generic wait finish on a latched fault; caller returns it. */
	return io_error || (ebu_read(EBU_NAND_WAIT) & WAIT_READY);
}

static u8 ro_read_byte(struct mtd_info *mtd)
{
	if (io_error)
		return 0xff;
	return __raw_readb((void __iomem *)(LTQ_NAND | CMD_CS));
}

static void ro_read_buf(struct mtd_info *mtd, u8 *buffer, int length)
{
	int i;

	for (i = 0; i < length; i++)
		buffer[i] = ro_read_byte(mtd);
}

static void ro_write_byte(struct mtd_info *mtd, u8 value)
{
	io_error = -EROFS;
}

static void ro_write_buf(struct mtd_info *mtd, const u8 *buffer, int length)
{
	io_error = -EROFS;
}

static int deny_erase(struct mtd_info *mtd, struct erase_info *info)
{
	return -EROFS;
}

static int deny_write(struct mtd_info *mtd, loff_t to, size_t length,
		      size_t *retlen, const u8 *buffer)
{
	*retlen = 0;
	return -EROFS;
}

static int deny_oob(struct mtd_info *mtd, loff_t to, struct mtd_oob_ops *ops)
{
	ops->retlen = ops->oobretlen = 0;
	return -EROFS;
}

static int deny_markbad(struct mtd_info *mtd, loff_t offset)
{
	return -EROFS;
}

static int deny_lock(struct mtd_info *mtd, loff_t offset, uint64_t length)
{
	return -EROFS;
}

/* The generic nand_init() entry intentionally does nothing. */
void board_nand_init(void)
{
}

int ltq_nand_ro_probe(void)
{
	struct mtd_info *mtd = nand_to_mtd(&ltq_chip);
	int ret;

	if (probe_attempted)
		return probe_status;
	probe_attempted = true;
	probe_status = -ENODEV;
	ret = enable_ebu();
	if (ret) {
		probe_status = ret;
		return ret;
	}
	setup_bus();
	ltq_chip.IO_ADDR_R = ltq_chip.IO_ADDR_W =
		(void __iomem *)(LTQ_NAND | CMD_CS);
	ltq_chip.cmd_ctrl = ro_cmd_ctrl;
	ltq_chip.select_chip = ro_select_chip;
	ltq_chip.dev_ready = ro_dev_ready;
	ltq_chip.read_byte = ro_read_byte;
	ltq_chip.read_buf = ro_read_buf;
	ltq_chip.write_byte = ro_write_byte;
	ltq_chip.write_buf = ro_write_buf;
	ltq_chip.chip_delay = 30;
	ltq_chip.options = NAND_ROM | NAND_KEEP_TIMINGS | NAND_SKIP_BBTSCAN |
		NAND_NO_SUBPAGE_WRITE;
	ltq_chip.bbt_options = NAND_BBT_SCAN2NDPAGE;
	ltq_chip.ecc.mode = NAND_ECC_SOFT;
	ltq_chip.ecc.size = 256;
	ltq_chip.ecc.strength = 1;
	ret = nand_scan_ident(mtd, 1, NULL);
	if (io_error)
		ret = io_error;
	if (ret)
		goto out;
	/* Geometry is proved by local dumps; unsupported chips are rejected. */
	if (mtd->size != 0x08000000 || mtd->writesize != 2048 ||
	    mtd->oobsize != 64 || mtd->erasesize != 0x20000 ||
	    !nand_is_slc(&ltq_chip) || (ltq_chip.options & NAND_BUSWIDTH_16)) {
		ret = -ENOTSUPP;
		goto out;
	}
	ret = nand_scan_tail(mtd);
	if (ret)
		goto out;
	if (ltq_chip.ecc.size != 256 || ltq_chip.ecc.bytes != 3 ||
	    ltq_chip.ecc.layout->eccbytes != 24 ||
	    ltq_chip.ecc.layout->eccpos[0] != 40 ||
	    ltq_chip.ecc.layout->eccpos[23] != 63) {
		ret = -ENOTSUPP;
		goto out;
	}
	mtd->flags &= ~MTD_WRITEABLE;
	mtd->_erase = deny_erase;
	mtd->_write = deny_write;
	mtd->_panic_write = deny_write;
	mtd->_write_oob = deny_oob;
	mtd->_block_markbad = deny_markbad;
	mtd->_lock = mtd->_unlock = deny_lock;
	ret = nand_register(0, mtd);
out:
	ro_select_chip(mtd, -1);
	probe_status = ret;
	return ret;
}

struct mtd_info *ltq_nand_ro_mtd(void)
{
	return probe_attempted && !probe_status ? nand_to_mtd(&ltq_chip) : NULL;
}

void ltq_nand_ro_print_id(void)
{
	int i;

	printf("NAND ID:");
	for (i = 0; i < ltq_chip.id.len; i++)
		printf(" %02x", ltq_chip.id.data[i]);
	puts("\n");
}

int ltq_nand_ro_isbad(u32 offset)
{
	struct mtd_info *mtd = ltq_nand_ro_mtd();
	int ret;

	if (!mtd || offset >= mtd->size || offset % mtd->erasesize)
		return -EINVAL;
	io_error = 0;
	ret = mtd_block_isbad(mtd, offset);
	return io_error ? io_error : ret;
}

int ltq_nand_ro_read_page(u32 offset, bool raw, u8 *data, u8 *oob)
{
	struct mtd_info *mtd = ltq_nand_ro_mtd();
	struct mtd_oob_ops ops = {0};
	int ret;

	if (!mtd || !data || !oob || offset % 2048 || offset >= mtd->size)
		return -EINVAL;
	io_error = 0;
	ops.mode = raw ? MTD_OPS_RAW : MTD_OPS_PLACE_OOB;
	ops.datbuf = data;
	ops.len = 2048;
	ops.oobbuf = oob;
	ops.ooblen = 64;
	ret = mtd_read_oob(mtd, offset, &ops);
	if (io_error)
		return io_error;
	if (ret && ret != -EUCLEAN)
		return ret;
	if (ops.retlen != 2048 || ops.oobretlen != 64)
		return -EIO;
	return 0;
}
