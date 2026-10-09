/* SPDX-License-Identifier: GPL-2.0+ */
 .set noreorder
 .section .text.entry,"ax"
 .globl _start
_start:
 /* SRAM code/stack are uncached. Interrupts remain disabled throughout. */
 mfc0 $8,$12
 li $9,~1
 and $8,$8,$9
 mtc0 $8,$12
 ehb
 li $sp,0xbe22f000
 addiu $sp,$sp,-32
#ifdef BUILD_SPL
 la $25,spl_main
#else
 la $8,__bss_start
 la $9,__bss_end
1:
 beq $8,$9,2f
 nop
 sw $zero,0($8)
 b 1b
 addiu $8,$8,4
2:
 la $25,tpl_main
#endif
 jr $25
 nop
#ifndef BUILD_SPL
 .section .text.handoff,"ax"
 .globl handoff
handoff:
 /* All TPL buffers and output use KSEG1; main performs its own tested
  * cache reset. Invalidate both 32 KiB primary tag arrays before entry.
  * Constants match P2812 34Kc's tested 32-byte lines. No writeback of
  * undefined cold cache contents. Cache physics is outside the model.
  */
 mtc0 $zero,$28,0
 mtc0 $zero,$28,2
 ehb
 li $8,0x80000000
 li $9,0x80008000
3:
 cache 0x08,0($8)
 cache 0x09,0($8)
 addiu $8,$8,32
 bne $8,$9,3b
 nop
 sync
 mfc0 $8,$16
 li $9,~7
 and $8,$8,$9
 ori $8,$8,3
 mtc0 $8,$16
 ehb
 li $25,0xa0100000
 jr $25
 nop
#endif
