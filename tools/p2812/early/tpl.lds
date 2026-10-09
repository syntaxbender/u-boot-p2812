/* SPDX-License-Identifier: GPL-2.0+ */
OUTPUT_ARCH(mips)
ENTRY(_start)
SECTIONS {
 . = 0xbe220800;
 .text : { *(.text.entry) *(.text*) }
 .rodata : { *(.rodata*) }
 .data : { *(.data*) }
 __image_end = .;
 . = ALIGN(16);
 __bss_start = .;
 .bss (NOLOAD) : { *(.bss*) *(COMMON) }
 . = ALIGN(16);
 __bss_end = .;
 ASSERT(__image_end <= 0xbe228000, "TPL exceeds fixed loaded NAND/SRAM extent")
 ASSERT(__bss_end < 0xbe22c000, "Reserve at least 12 KiB below the SRAM stack top")
 /DISCARD/ : { *(.MIPS.abiflags) *(.reginfo) *(.pdr) *(.comment) *(.note*) *(.eh_frame*) *(.gnu*) }
}
