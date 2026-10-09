P2812HNU-F1 source-built NAND boot profile

The published profile has a five-second cancellable autoboot, nandbootrun
from the 5 MiB NAND kernel partition, rambootrun and a 16 MiB decode limit.
The bootloader NAND driver is read-only; no saveenv/erase/program command
is provided. Linux itself can write its normal filesystems.

The v2026.10 profile at source revision91ba975c was installed and exercised
with OpenWrt25.12.0, Ethernet, Wi-Fi and VDSL Internet in the recorded local
project tests. CI does not perform hardware tests. A newly built upstream
version is not automatically hardware-validated by those earlier results.

All SPL/TPL/main code is built from source; no modem backup, legacy binary
prefix, calibration data or credentials are build inputs. Historical vendor
source attribution and license notices are retained in tools/p2812.

The automation branch holds p2812-ci/port.patch and a scheduled workflow.
It selects upstream stable tags, applies the port to a fresh source tree,
builds twice and publishes only after build/model/reproducibility checks.
An incompatible patch or upstream API stops publication; it is not auto-ported.

The builder archive SHA, OCI image identities, public Linux test images and
Unicorn wheel are pinned. The profile records the selected upstream commit
and SOURCE_DATE_EPOCH. The v2026.10 reference keeps its historical epoch;
new stable versions use their upstream commit time. Containers have no
network or serial/device mounts. Models stop before Linux executes.

Entry point from a clean committed prepared source tree:
  python3 -B tools/p2812/nandfinal1/run.py /fresh/run1 --image zyxel-uboot-modern:20261009 --wheel /inputs/unicorn.whl --sysupgrade /inputs/sysupgrade.bin --initramfs /inputs/initramfs.bin
Repeat with /fresh/run2, then tools/p2812/nandfinal1/compare.py run1 run2.

The generated manifest describes this CI execution, including no hardware
access. The release build-manifest separately indicates whether its firmware
hashes match the previously hardware-tested v2026.10 baseline. Bootloader
artifacts do not include the separate OpenWrt OS/kernel/Wi-Fi calibration.
