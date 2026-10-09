#!/bin/bash
# Run only inside the pinned Linux/amd64 builder. No device mounts or network.
set -euo pipefail
export SOURCE_DATE_EPOCH=1791504000 TZ=UTC LC_ALL=C PYTHONDONTWRITEBYTECODE=1
export KBUILD_BUILD_USER=research KBUILD_BUILD_HOST=local
src=/tmp/p2812-nandboot-src
obj=/tmp/p2812-nandboot-obj
out=/results
kit=/source/tools/p2812
mkdir "$src" "$obj" "$out/main" "$out/early" "$out/package"
tar --exclude=.git -C /source -cf - . | tar -C "$src" -xf -
mips-linux-gnu-gcc --version > "$out/main/compiler.txt"
mips-linux-gnu-ld --version > "$out/main/binutils.txt"
cp /modern-build-packages.txt "$out/main/host-packages.txt"
make -C "$src" O="$obj" CROSS_COMPILE=mips-linux-gnu- \
    p2812hnuf1_nandboot2_defconfig > "$out/main/build.log" 2>&1
if ! make -C "$src" -j4 O="$obj" CROSS_COMPILE=mips-linux-gnu- all \
        >> "$out/main/build.log" 2>&1; then
    tail -60 "$out/main/build.log"
    exit 1
fi
for file in u-boot u-boot.bin u-boot-nodtb.bin u-boot.dtb u-boot.map System.map .config; do
    test ! -f "$obj/$file" || cp "$obj/$file" "$out/main/$file"
done
python3 "$kit/package.py" main "$out"
cp -R "$kit/early" /tmp/nandsource4
early=/tmp/nandsource4
python3 - "$out/package/u-boot.bin" <<'PY'
from pathlib import Path
import sys,zlib
data=Path(sys.argv[1]).read_bytes()
Path('/tmp/nandsource4/generated.h').write_text(
    '#define MAIN_BYTES '+str(len(data))+'u\n#define MAIN_EXPECTED_CRC 0x%08xu\n'%zlib.crc32(data))
PY
cd "$out/early"
flags='-EB -march=mips32r2 -mtune=34kc -msoft-float -mno-abicalls -fno-pic -G0 -Os -ffreestanding -fno-builtin -fno-stack-protector -ffunction-sections -fdata-sections -fstack-usage -Wall -Wextra -Werror -Wno-unused-parameter'
for file in runtime ebu clock soc trace memory nand image tpl vendor/nand_ecc vendor/lzo1x_decompress; do
    name=$(basename "$file")
    mips-linux-gnu-gcc $flags -I"$early" -I"$early/include" -c "$early/$file.c" -o "$name.o"
done
mips-linux-gnu-gcc $flags -c "$early/start.S" -o start.o
mips-linux-gnu-ld -EB --gc-sections -T "$early/tpl.lds" -Map tpl.map -o tpl.elf \
    start.o runtime.o ebu.o clock.o soc.o trace.o memory.o nand.o image.o tpl.o nand_ecc.o lzo1x_decompress.o
mips-linux-gnu-objcopy -O binary tpl.elf tpl.bin
python3 - <<'PY'
from pathlib import Path
import zlib
data=Path('tpl.bin').read_bytes()
if len(data)>0x7800:raise RuntimeError('TPL layout overflow')
data=data.ljust(0x7800,b'\xff')
Path('tpl-padded.bin').write_bytes(data)
with Path('/tmp/nandsource4/generated.h').open('a') as stream:
    stream.write('#define TPL_CRC 0x%08xu\n'%zlib.crc32(data))
PY
mips-linux-gnu-gcc $flags -DBUILD_SPL -c "$early/start.S" -o spl-start.o
mips-linux-gnu-gcc $flags -I"$early" -I"$early/include" -c "$early/spl.c" -o spl.o
mips-linux-gnu-ld -EB --gc-sections -T "$early/spl.lds" -Map spl.map -o spl.elf \
    spl-start.o spl.o runtime.o ebu.o nand_ecc.o
mips-linux-gnu-objcopy -O binary spl.elf spl.bin
cp "$early/generated.h" generated.h
for stage in spl tpl; do
    mips-linux-gnu-nm -n "$stage.elf" > "$stage-symbols.txt"
    mips-linux-gnu-objdump -d "$stage.elf" > "$stage-disassembly.txt"
    mips-linux-gnu-readelf -h -l -S "$stage.elf" > "$stage-readelf.txt"
done
python3 "$kit/package.py" chain "$out"
python3 "$kit/tests/test_modern_nandsource4_native.py" "$out" > "$out/native-tests.log" 2>&1
python3 "$kit/package.py" oob "$out"
python3 "$kit/tests/test_modern_nandsource4_mips.py" "$out" > "$out/mips-tests.log" 2>&1
python3 "$kit/tests/test_modern_driver.py" --source "$src" > "$out/uart-clock-tests.log" 2>&1
python3 "$kit/tests/test_modern_nandro_driver.py" --source "$src" > "$out/nand-driver-tests.log" 2>&1
python3 "$kit/package.py" audit "$out"
printf 'source_only_build_and_tests_completed\n' > "$out/status.txt"
