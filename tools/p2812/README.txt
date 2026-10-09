P2812HNU-F1 — source-only research baseline

Official upstream base
https://github.com/u-boot/u-boot
v2026.10, commit 5508406582f6f7120dce0c4b3059819a41788db2

This branch contains the final NANDBOOT2 main port and the source-built
NANDSOURCE4 early loader. No intermediate experimental defconfigs, binary
bootloader prefixes or private device backups are needed to build it.
The main's historical RAM-only labels are retained to reproduce the exact
validated binary. They do not describe the origin of the NAND SPL/TPL chain.

Build
Load the frozen Linux/amd64 builder archive, after checking its SHA256
against baseline.json. The archive SHA and Docker image ID are different
identities; verify both. Obtain the pinned public Unicorn wheel listed in
baseline.json, and run from a clean committed checkout:

  python3 -B tools/p2812/run.py /absolute/fresh/run1 --wheel /path/to/unicorn.whl
  python3 -B tools/p2812/run.py /absolute/fresh/run2 --wheel /path/to/unicorn.whl
  python3 -B tools/p2812/compare.py /absolute/fresh/run1 /absolute/fresh/run2

--check-only verifies source and wheel without starting Docker. The build
container has no network or device mounts. The complete committed source
archive, command, input identity, logs, hashes and test results are retained
for every run. Output must be outside the checkout; an existing run is never
overwritten. SOURCE_DATE_EPOCH=1791504000 fixes the established build identity.

Outputs
package/u-boot.bin: main U-Boot (248744 bytes).
early/spl.bin and tpl.bin: entirely rebuilt early stages.
package/nandsource4-main-area.img: ROM NVB + SPL + TPL + LZO main (153055 bytes).
package/boot-main-256k.bin: main area padded to the fixed boot extent.
package/boot-main-oob.bin: 128 physical pages of 2048 main + 64 OOB bytes.
OOB[0..39]=FF, OOB[40..63]=8 Hamming 1/256 ECC steps. No remapping.
These are research artifacts, not a generic flashing procedure.

Validation and limits
All seven binary reference hashes must match. Each clean build executes the
62 linked MIPS SPL/TPL cases and the existing native ASan/UBSan ECC/LZO and
UART/clock/NAND driver harnesses. Models use small measured register fixtures
and explicit synthetic NAND/DDR fault injection; they do not prove physical
timing, analogue behavior or Boot ROM recovery. Early sources and upstream/
historical provenance, licenses and vendor changes are retained.

Hardware evidence as of 18 October 2026
The reference raw NAND boot extent was programmed and fully read back.
After a user-reported normal power cycle, the modern prompt and successful
9079614-byte kernel decode rehearsal were observed. The full ROM-start trace
was not captured. Default nandbootcheck returns before Linux; this is not a
production automatic OpenWrt boot build. Production PREP/GO remains separate
work. No hardware operations occur in this build/CI workflow.

CI preparation
.github/workflows/p2812.yml uses this same command twice, compares binaries
and publishes build artifacts. A downloadable frozen builder archive must
first be made available and its URL set as P2812_BUILDER_ARCHIVE_URL in the
repository variables. The archive is verified against its fixed SHA before
docker load. This local branch and the builder have not been published;
GitHub-hosted CI execution is not yet verified. The local workflow does not
depend on a mutable package repository or a tag-only compiler image.
