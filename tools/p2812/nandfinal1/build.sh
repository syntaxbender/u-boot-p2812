#!/bin/bash
set -euo pipefail
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1791504000}" TZ=UTC LC_ALL=C PYTHONDONTWRITEBYTECODE=1
kit=/source/tools/p2812/nandfinal1
out=/results
bash "$kit/build-main.sh"
python3 "$kit/fixtures.py" "$out/fixtures" --sysupgrade /inputs/sysupgrade.bin \
    --initramfs /inputs/initramfs.bin > "$out/fixtures.log" 2>&1
python3 "$kit/test_mips.py" "$out" --fixtures "$out/fixtures" > "$out/linuxrun-mips-tests.log" 2>&1
python3 "$kit/test_autoboot.py" "$out" > "$out/autoboot-tests.log" 2>&1
python3 "$kit/test_startup.py" "$out/main" > "$out/startup-tests.log" 2>&1
mkdir "$out/package"
python3 "$kit/package.py" main "$out"
bash "$kit/build-early.sh"
python3 "$kit/package.py" chain "$out"
python3 "$kit/test_early_native.py" "$out" > "$out/native-tests.log" 2>&1
python3 "$kit/package.py" oob "$out"
python3 "$kit/test_early_mips.py" "$out" > "$out/mips-tests.log" 2>&1
python3 "$kit/package.py" audit "$out"
printf 'nandfinal1_complete_build_and_tests_passed\n' > "$out/status.txt"
