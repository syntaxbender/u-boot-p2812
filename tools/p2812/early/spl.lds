/* SPDX-License-Identifier: GPL-2.0+ */
OUTPUT_ARCH(mips)
ENTRY(_start)
SECTIONS {
 . = 0xbe220000;
 .text : { *(.text.entry) *(.text*) }
 .rodata : { *(.rodata*) }
 .data : { *(.data*) }
 __image_end = .;
 .bss (NOLOAD) : { *(.bss*) *(COMMON) }
 ASSERT(SIZEOF(.bss) == 0, "Bootstrap must not depend on zeroed BSS")
 ASSERT(__image_end <= 0xbe2207e8, "ROM commands + bootstrap exceed first page")
 /DISCARD/ : { *(.MIPS.abiflags) *(.reginfo) *(.pdr) *(.comment) *(.note*) *(.eh_frame*) *(.gnu*) }
}
