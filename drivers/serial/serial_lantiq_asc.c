// SPDX-License-Identifier: GPL-2.0+
/*
 * Adapted from OpenWrt U-Boot 2013.10 drivers/serial/serial_lantiq.c.
 * Copyright (C) 2010 Thomas Langer <thomas.langer@lantiq.com>
 * Copyright (C) 2011-2013 Daniel Schwierzeck
 * Native-endian MMIO is intentional on this big-endian SoC.
 */
#include <dm.h>
#include <errno.h>
#include <serial.h>
#include <linux/bitops.h>
#include <asm/addrspace.h>
#include <asm/io.h>
#include <mach/vrx200.h>

#define ASC_CLC		0x00
#define ASC_PISEL	0x04
#define ASC_MCON		0x10
#define ASC_WHBSTATE	0x18
#define ASC_TBUF		0x23
#define ASC_RBUF		0x27
#define ASC_RXFCON	0x40
#define ASC_TXFCON	0x44
#define ASC_FSTAT	0x48
#define ASC_BG		0x50
#define ASC_FDV		0x58
#define ASC_RUN		BIT(15)
#define ASC_FRACTIONAL	BIT(9)

struct ltq_asc_priv {
	void __iomem *base;
};

static u32 asc_read(struct ltq_asc_priv *priv, unsigned int offset)
{
	return __raw_readl(priv->base + offset);
}

static void asc_write(struct ltq_asc_priv *priv, unsigned int offset, u32 value)
{
	__raw_writel(value, priv->base + offset);
}

static int ltq_asc_setbrg(struct udevice *dev, int baudrate)
{
	struct ltq_asc_priv *priv = dev_get_priv(dev);
	u32 c = ltq_vrx200_bus_rate() / (16 * 512);
	u32 target, best_diff, bg = 0, fdv = 0, i;

	if (!c || baudrate <= 0)
		return -EINVAL;
	target = baudrate;
	best_diff = target;
	for (i = 512; i; i--) {
		u32 divisor = i * c / target;
		u32 actual, diff;

		if (!divisor || divisor > 65536)
			continue;
		actual = i * c / divisor;
		diff = actual > target ? actual - target : target - actual;
		if (diff < best_diff) {
			best_diff = diff;
			bg = divisor - 1;
			fdv = i;
			if (!diff)
				break;
		}
	}
	if (!fdv)
		return -EINVAL;
	asc_write(priv, ASC_MCON, asc_read(priv, ASC_MCON) & ~ASC_RUN);
	asc_write(priv, ASC_FDV, fdv);
	asc_write(priv, ASC_BG, bg);
	asc_write(priv, ASC_MCON, asc_read(priv, ASC_MCON) |
		  ASC_FRACTIONAL | ASC_RUN);
	return 0;
}

static int ltq_asc_putc(struct udevice *dev, const char ch)
{
	struct ltq_asc_priv *priv = dev_get_priv(dev);

	if (!((asc_read(priv, ASC_FSTAT) >> 24) & 0x3f))
		return -EAGAIN;
	__raw_writeb(ch, priv->base + ASC_TBUF);
	return 0;
}

static int ltq_asc_getc(struct udevice *dev)
{
	struct ltq_asc_priv *priv = dev_get_priv(dev);

	if (!(asc_read(priv, ASC_FSTAT) & 0x3f))
		return -EAGAIN;
	return __raw_readb(priv->base + ASC_RBUF);
}

static int ltq_asc_pending(struct udevice *dev, bool input)
{
	struct ltq_asc_priv *priv = dev_get_priv(dev);
	u32 status = asc_read(priv, ASC_FSTAT);

	return input ? status & 0x3f : (status >> 8) & 0x3f;
}

static int ltq_asc_probe(struct udevice *dev)
{
	struct ltq_asc_priv *priv = dev_get_priv(dev);
	fdt_addr_t address = dev_read_addr(dev);

	if (address == FDT_ADDR_T_NONE)
		return -EINVAL;
	priv->base = (void *)CKSEG1ADDR(address);
	/* Touch only the ASC block. ROM has already configured its pins/clock. */
	asc_write(priv, ASC_CLC, 0x100);
	asc_write(priv, ASC_MCON, 0);
	asc_write(priv, ASC_PISEL, 0);
	asc_write(priv, ASC_RXFCON, asc_read(priv, ASC_RXFCON) | 3);
	asc_write(priv, ASC_TXFCON, asc_read(priv, ASC_TXFCON) | 3);
	asc_write(priv, ASC_WHBSTATE, BIT(1));
	return 0;
}

static const struct dm_serial_ops ltq_asc_ops = {
	.putc = ltq_asc_putc,
	.getc = ltq_asc_getc,
	.pending = ltq_asc_pending,
	.setbrg = ltq_asc_setbrg,
};

static const struct udevice_id ltq_asc_ids[] = {
	{ .compatible = "lantiq,asc" },
	{ }
};

U_BOOT_DRIVER(serial_lantiq_asc) = {
	.name = "serial_lantiq_asc",
	.id = UCLASS_SERIAL,
	.of_match = ltq_asc_ids,
	.probe = ltq_asc_probe,
	.priv_auto = sizeof(struct ltq_asc_priv),
	.ops = &ltq_asc_ops,
	.flags = DM_FLAG_PRE_RELOC,
};

#ifdef CONFIG_DEBUG_UART_LANTIQ_ASC
#include <debug_uart.h>

static inline void _debug_uart_init(void)
{
	/* Preserve the known working ROM UART configuration. */
}

static inline void _debug_uart_putc(int ch)
{
	void __iomem *base = (void *)CONFIG_DEBUG_UART_BASE;

	while (!((__raw_readl(base + ASC_FSTAT) >> 24) & 0x3f))
		;
	__raw_writeb(ch, base + ASC_TBUF);
}

DEBUG_UART_FUNCS
#endif
